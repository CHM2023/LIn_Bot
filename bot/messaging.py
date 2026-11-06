"""Find conversations with a new incoming message and answer them.

The flow for one poll:

1. Read the conversation list (name + preview text of each conversation).
2. Compare it with what we saw last time. LinkedIn sorts the list by most
   recent activity, so new activity is always at the top: we walk down from
   the top and stop at the first conversation that has not changed.
3. Open each changed conversation, check that the thread on screen really is
   the one we clicked, and reply only if its last message is from the other
   person.

The very first poll only takes a snapshot ("baseline"): whatever is already in
the inbox is history, not something the bot should suddenly answer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import dom
from .browser import detect_owner_name, first_match, goto, pause
from .config import Settings
from .replies import Message, Responder
from .state import State

log = logging.getLogger("bot.messaging")

MAX_TRIES_PER_CONVERSATION = 3
BASELINE_CHECKS = 3


@dataclass(frozen=True)
class Card:
    """One row of the conversation list."""

    index: int
    mode: str  # how the row was found: "label" (structure) or "class" (fallback)
    name: str
    snippet: str

    @property
    def selector(self) -> str:
        return dom.CONVERSATION_ROWS if self.mode == "label" else dom.CONVERSATION_ROWS_BY_CLASS


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip().casefold()


def same_person(a: str, b: str) -> bool:
    a, b = _normalise(a), _normalise(b)
    return bool(a) and bool(b) and (a == b or a.startswith(b) or b.startswith(a))


def same_name(a: str, b: str) -> bool:
    """Exactly the same name, ignoring case and spacing."""
    return _normalise(a) == _normalise(b)


# --- 1. read the list --------------------------------------------------------
def list_conversations(page: Page, settings: Settings) -> tuple[bool, list[Card]]:
    """Return (did the messaging page load, the conversations on screen)."""
    try:
        # The list fills in a few seconds after the page itself.
        page.wait_for_selector(
            f"{dom.CONVERSATION_ROWS}, {dom.CONVERSATION_ROWS_BY_CLASS}",
            state="attached",
            timeout=settings.render_wait_ms * 2,
        )
        pause(page, settings, 0.8, 1.5)
    except PlaywrightTimeout:
        pass
    result = page.evaluate(dom.LIST_CONVERSATIONS_JS, dom.CONVERSATION)
    cards = [Card(**card) for card in result["cards"]]
    page_loaded = bool(result["listFound"]) or page.locator(dom.COMPOSE_BOX).count() > 0
    return page_loaded, cards[: settings.max_conversations]


# --- 2. decide which conversations deserve a look ----------------------------
def baseline_ready(page_loaded: bool, cards: list[Card], state: State) -> bool:
    """Only trust the first snapshot of the inbox when it is clearly complete.

    If the snapshot were taken while the list was still loading, every old
    conversation would look new on the next poll, and the bot would answer
    all of them. So an empty or half-rendered list has to be seen several
    polls in a row before it is believed.
    """
    state.baseline_checks += 1
    if cards and cards[0].index == 0:
        return True
    if state.baseline_checks >= BASELINE_CHECKS and (cards or page_loaded):
        return True
    log.info(
        "Inbox not fully visible yet (check %d/%d); waiting before taking the baseline.",
        state.baseline_checks,
        BASELINE_CHECKS,
    )
    return False


def pick_candidates(cards: list[Card], state: State, settings: Settings) -> list[Card]:
    """Conversations that changed since the last poll and may need a reply."""
    # Many rows appearing when none was ever seen is the list finishing to
    # load, not several first-ever messages at once: treat it as the baseline.
    late_load = not state.seen and len(cards) > 2
    if not state.baselined or late_load:
        for card in cards:
            state.remember(card.name, card.snippet)
        state.baselined = True
        log.info("Baseline taken: %d existing conversation(s) left untouched.", len(cards))
        return []

    candidates = []
    for card in cards:
        if not state.is_new(card.name, card.snippet):
            break  # sorted by recency: nothing below an unchanged conversation is new
        if card.snippet.startswith(settings.self_prefixes):
            state.remember(card.name, card.snippet)  # the last message is our own
            continue
        candidates.append(card)
    return candidates


# --- 3. open, read, reply ----------------------------------------------------
def _clues(message: dict, has_marker: bool, owner_name: str) -> list[bool]:
    """Independent signs that a message is incoming, most reliable first."""
    clues = []
    if message.get("mine") is not None:  # sender's profile id vs the account's id
        clues.append(not message["mine"])
    if has_marker:  # LinkedIn's CSS class for "written by the other person"
        clues.append(bool(message.get("marked")))
    if owner_name and message.get("sender"):  # sender's name vs the account owner's
        clues.append(not same_name(message["sender"], owner_name))
    return clues


def resolve_directions(raw: list[dict], owner_name: str) -> list[Message] | None:
    """Work out, for each message, whether the other person wrote it.

    Returns None when the last message cannot be attributed with certainty
    (no clue at all, or clues that contradict each other). The caller then
    does not reply: guessing wrong would mean answering our own message.
    """
    if not raw:
        return None
    has_marker = any(message.get("marked") for message in raw)
    if len(set(_clues(raw[-1], has_marker, owner_name))) != 1:
        return None
    messages = []
    for message in raw:
        clues = _clues(message, has_marker, owner_name)
        messages.append(Message(message["sender"], message["text"], clues[0] if clues else True))
    return messages


def _thread_matches(card: Card, raw: dict) -> bool:
    """Is the thread on screen the conversation we clicked?

    Both available clues must agree: the name above the thread, and the names
    printed next to the messages. With no clue at all the answer is no.
    """
    if raw["title"] and not same_person(raw["title"], card.name):
        return False
    senders = {message["sender"] for message in raw["messages"] if message["sender"]}
    if senders:
        return any(same_person(sender, card.name) for sender in senders)
    return bool(raw["title"])


def read_thread(page: Page) -> dict:
    return page.evaluate(dom.READ_THREAD_JS, dom.THREAD)


def open_conversation(page: Page, card: Card, settings: Settings) -> dict | None:
    """Click a conversation and return its thread once it has really loaded.

    The thread pane keeps showing the previous conversation for a moment after
    the click. Replying then would send the answer to the wrong person, so the
    thread is only returned when it matches the clicked row and has stopped
    changing between two reads.
    """
    row = page.locator(card.selector).nth(card.index)
    try:
        label = row.locator("label[aria-label]")
        labelled = label.first.get_attribute("aria-label") if label.count() else ""
        row_text = _normalise(f"{labelled or ''} {row.inner_text(timeout=3_000)}")
    except PlaywrightError:
        return None
    if _normalise(card.name) not in row_text:
        return None  # the list moved since we read it; try again next poll

    target = first_match(row, dom.CONVERSATION_LINK) or row
    try:
        target.scroll_into_view_if_needed(timeout=3_000)
        target.click(timeout=3_000)
    except PlaywrightError:
        try:
            target.evaluate("element => element.click()")
        except PlaywrightError:
            return None

    step_ms = max(60, 450 * settings.pace)
    previous = None
    for _ in range(20):
        page.wait_for_timeout(step_ms)
        raw = read_thread(page)
        if raw["messages"] and raw == previous and _thread_matches(card, raw):
            return raw
        previous = raw
    return None


def message_box(page: Page) -> Locator | None:
    """The box to type in. None unless there is exactly one on screen
    (sponsored and closed conversations have none)."""
    editors = page.locator(dom.COMPOSE_BOX)
    visible = [editors.nth(i) for i in range(editors.count()) if editors.nth(i).is_visible()]
    return visible[0] if len(visible) == 1 else None


def _clear_box(page: Page) -> None:
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")


def _send_button(page: Page, box: Locator) -> Locator | None:
    """The Send button that belongs to this message box."""
    form = box.locator("xpath=ancestor::form[1]")
    return first_match(form if form.count() else page.locator("main"), dom.SEND_BUTTON)


def _ready_to_send(page: Page, box: Locator) -> bool:
    """LinkedIn enables Send a moment after its editor has registered the text."""
    button = _send_button(page, box)
    if button is None:
        return True  # no button to wait for: the message goes out with Enter
    for _ in range(20):
        if button.is_enabled():
            return True
        page.wait_for_timeout(150)
    return False


def _write(page: Page, box: Locator, text: str, settings: Settings) -> str:
    """Put `text` in the focused message box.

    Returns how it was entered ("insert" or "type"), or "" when neither way
    left the box showing the text with Send ready. Inserting the text in one
    go suits rich editors that ignore single key presses; typing key by key is
    the fallback for editors that want the opposite.
    """
    delay = settings.typing_delay_ms * settings.pace
    methods = {
        "insert": page.keyboard.insert_text,
        "type": lambda line: page.keyboard.type(line, delay=delay),
    }
    for name, enter in methods.items():
        for number, line in enumerate(text.splitlines()):
            if number:
                page.keyboard.press("Shift+Enter")  # new line without sending
            if line:
                enter(line)
        if _normalise(box.inner_text()) == _normalise(text) and _ready_to_send(page, box):
            return name
        _clear_box(page)
    return ""


def send_reply(page: Page, text: str, settings: Settings) -> bool:
    """Type the reply into the message box and send it. True when it went out."""
    box = message_box(page)
    if box is None:
        return False
    try:
        box.click(timeout=5_000)
    except PlaywrightError:
        return False
    _clear_box(page)  # a leftover draft must not be sent along
    if not _write(page, box, text, settings):
        return False
    pause(page, settings, 0.3, 0.8)

    button = _send_button(page, box)
    if button is None:
        page.keyboard.press("Enter")
    else:
        try:
            button.click(timeout=5_000)  # waits for the button to become enabled
        except PlaywrightError:
            return False
    # LinkedIn empties the box once the message is on its way.
    for _ in range(25):
        page.wait_for_timeout(200)
        if not box.inner_text().strip():
            return True
    return False


def typing_check(page: Page, settings: Settings) -> dict:
    """Diagnostic: type a word in the open conversation's message box, see
    whether the box shows it and Send becomes clickable, then erase it.
    Nothing is sent.
    """
    box = message_box(page)
    if box is None:
        return {"box": False}
    if box.inner_text().strip():
        return {"box": True, "skipped": "there is an unsent draft in the box; left untouched"}
    box.click(timeout=5_000)
    method = _write(page, box, "test", settings)
    _clear_box(page)
    page.wait_for_timeout(300)
    return {
        "box": True,
        "send_button": _send_button(page, box) is not None,
        "ready_to_send_after": method or "NEITHER METHOD (the bot could not send)",
        "erased": not box.inner_text().strip(),
    }


def confirm_sent(page: Page, reply: str, owner: str, settings: Settings) -> str:
    """Read the thread back after sending.

    "confirmed"   the reply is the last message and is recognised as ours
    "misread"     it is there, but the bot takes it for someone else's message
    "unconfirmed" it did not show up in the thread in time
    """
    seen = False
    for _ in range(15):
        page.wait_for_timeout(max(60, 300 * settings.pace))
        raw = read_thread(page)["messages"]
        if not raw or _normalise(raw[-1]["text"]) != _normalise(reply):
            continue
        seen = True
        messages = resolve_directions(raw, owner)
        if messages is not None and not messages[-1].incoming:
            return "confirmed"
    return "misread" if seen else "unconfirmed"


def thread_key(url: str, fallback: str) -> str:
    match = re.search(r"/messaging/thread/([^/?#]+)", url)
    return match.group(1) if match else fallback


def _failed(card: Card, state: State, reason: str) -> str:
    tries = state.note_failure(card.name)
    if tries >= MAX_TRIES_PER_CONVERSATION:
        log.warning("Giving up on the conversation with %s: %s.", card.name, reason)
        state.remember(card.name, card.snippet)
    else:
        log.warning(
            "Conversation with %s: %s (try %d/%d).",
            card.name,
            reason,
            tries,
            MAX_TRIES_PER_CONVERSATION,
        )
    return "failed"


def answer(
    page: Page, card: Card, settings: Settings, state: State, responder: Responder, owner: str
) -> str:
    """Handle one changed conversation. Returns what happened, for the log."""
    raw = open_conversation(page, card, settings)
    if raw is None:
        return _failed(card, state, "could not open it or confirm the right thread is on screen")
    messages = resolve_directions(raw["messages"], owner)
    if messages is None:
        return _failed(card, state, "could not tell who wrote the last message")

    thread = thread_key(page.url, card.name)
    last = messages[-1]
    # Second, independent check against answering ourselves: the last message
    # is word for word the reply we sent last in this conversation.
    is_own_reply = _normalise(last.text) == _normalise(state.last_reply.get(thread, "\0"))
    if not last.incoming or is_own_reply:
        state.remember(card.name, card.snippet)
        return "nothing to answer"

    if message_box(page) is None:
        log.info("Conversation with %s cannot be replied to (no message box); skipping.", card.name)
        state.remember(card.name, card.snippet)
        return "no message box"
    if state.replies_last_hour(thread) >= settings.max_replies_per_thread_per_hour:
        log.warning("Reply limit reached for the conversation with %s; leaving it.", card.name)
        state.remember(card.name, card.snippet)
        return "thread limit"

    reply = responder.reply(
        card.name,
        messages[-settings.history_messages :],
        owner=owner,
        first_reply=not state.has_replied(thread),
    )
    if settings.dry_run:
        log.info("[dry-run] would reply to %s: %s", card.name, reply)
        state.remember(card.name, card.snippet)
        return "dry run"

    pause(page, settings, 1.0, 2.5)
    if not send_reply(page, reply, settings):
        return _failed(card, state, "the reply could not be sent")
    state.record_reply(thread_key(page.url, thread), reply)
    state.remember(card.name, card.snippet)

    outcome = confirm_sent(page, reply, owner, settings)
    log.info("Replied to %s (%s): %s", card.name, outcome, reply)
    if outcome == "misread":
        log.error(
            "The bot does not recognise its own message as its own. It will not answer "
            "itself (replies are also compared by text), but run `python -m bot doctor`."
        )
    return "replied"


def process_inbox(page: Page, settings: Settings, state: State, responder: Responder) -> int:
    """One pass over the inbox. Returns how many replies were sent."""
    if state.replies_last_hour() >= settings.max_replies_per_hour:
        log.warning("Hourly reply limit (%d) reached; pausing replies.", settings.max_replies_per_hour)
        return 0

    owner = settings.owner_name or state.owner_name or detect_owner_name(page)
    attempted: set[str] = set()
    sent = 0
    while sent < settings.max_replies_per_cycle:
        # Start from a fresh list every time: sending a reply reorders it.
        goto(page, settings, dom.MESSAGING_PATH)
        page_loaded, cards = list_conversations(page, settings)
        if not cards and not page_loaded:
            log.warning("Messaging page not recognised. Run `python -m bot doctor`.")
            break
        if not state.baselined and not baseline_ready(page_loaded, cards, state):
            break
        candidates = [c for c in pick_candidates(cards, state, settings) if c.name not in attempted]
        state.save()
        if not candidates:
            break
        card = candidates[-1]  # oldest waiting conversation first
        attempted.add(card.name)
        owner = owner or detect_owner_name(page)
        outcome = answer(page, card, settings, state, responder, owner)
        log.debug("Conversation with %s: %s", card.name, outcome)
        if outcome == "replied":
            sent += 1
        state.save()
    return sent
