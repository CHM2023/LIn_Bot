"""Accept pending connection requests."""

from __future__ import annotations

import logging
import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import dom
from .browser import goto, pause
from .config import Settings
from .state import State

log = logging.getLogger("bot.invitations")

# The same page also lists invitations to newsletters, company pages and
# events. Their buttons say "Accept" too. The task is to accept *connection*
# requests only, so those are recognised by their button label or card text.
NOT_A_CONNECTION_LABEL = re.compile(r"\b(subscribe|follow|attend)\b|newsletter", re.I)
NOT_A_CONNECTION_CARD = re.compile(r"invited you to (follow|subscribe|attend)", re.I)


def _accept_words(settings: Settings) -> str:
    return "|".join(re.escape(word) for word in settings.accept_words)


def _accept_pattern(settings: Settings) -> re.Pattern:
    # Not anchored at the start: LinkedIn sometimes repeats a label for
    # screen readers. "\b" keeps "Accepted" from matching.
    return re.compile(rf"\b(?:{_accept_words(settings)})\b", re.I)


def inviter_name(label: str, settings: Settings, card_text: str = "") -> str:
    """'Accept Jane Doe’s invitation' -> 'Jane Doe'.

    When the button label carries no name, the first line of the invitation
    card is used. The name is only for the log: it never decides anything.
    """
    words = _accept_words(settings)
    match = re.search(rf"(?:(?:{words})\s+)+(.+?)(?:[’']s)?\s+invitation", label, re.I)
    if match:
        return match.group(1).strip()
    match = re.search(r"invitation (?:from|de)\s+(.+)$", label, re.I)
    if match:
        return match.group(1).strip()
    for line in card_text.splitlines():
        if line.strip() and not _accept_pattern(settings).search(line):
            return line.strip()[:60]
    return "a new connection"


def find_accept_buttons(page: Page, settings: Settings) -> list[tuple[Locator, str]]:
    """Accept buttons for connection requests, each with the inviter's name."""
    buttons = page.get_by_role("button", name=_accept_pattern(settings))
    found = []
    for index in range(buttons.count()):
        button = buttons.nth(index)
        label = (button.get_attribute("aria-label") or button.inner_text() or "").strip()
        if NOT_A_CONNECTION_LABEL.search(label):
            continue
        card_text = button.evaluate(dom.INVITATION_CARD_TEXT_JS, _accept_pattern(settings).pattern)
        if NOT_A_CONNECTION_CARD.search(card_text):
            continue
        found.append((button, inviter_name(label, settings, card_text)))
    return found


def accept_pending(page: Page, settings: Settings, state: State) -> list[str]:
    """Open the invitations page and accept every pending connection request.

    Returns the names accepted (in a dry run: the names that would be).
    """
    goto(page, settings, dom.INVITATIONS_PATH)
    # The invitation cards render after the page shell. Having none is the
    # normal case, so a timeout here is not an error.
    try:
        page.get_by_role("button", name=_accept_pattern(settings)).first.wait_for(
            state="visible", timeout=settings.render_wait_ms
        )
    except PlaywrightTimeout:
        return []

    if settings.dry_run:
        names = [name for _, name in find_accept_buttons(page, settings)]
        for name in names:
            log.info("[dry-run] would accept the invitation from %s", name)
        return names

    accepted: list[str] = []
    while len(accepted) < settings.max_accepts_per_cycle:
        pending = find_accept_buttons(page, settings)
        if not pending:
            break
        button, name = pending[0]
        try:
            button.click(timeout=5_000)
        except PlaywrightError as error:
            log.warning("Could not click Accept for %s: %s", name, str(error).splitlines()[0])
            break
        if not _wait_until_fewer(page, settings, len(pending)):
            # The card is still there: stop rather than click it in a loop.
            log.warning("Clicked Accept for %s but the invitation is still listed.", name)
            break
        state.record_accept(name)
        accepted.append(name)
        log.info("Accepted the invitation from %s", name)
        pause(page, settings, 0.8, 2.0)
    return accepted


def _wait_until_fewer(page: Page, settings: Settings, before: int) -> bool:
    for _ in range(25):
        page.wait_for_timeout(200)
        if len(find_accept_buttons(page, settings)) < before:
            return True
    return False
