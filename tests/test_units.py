"""Fast tests for the decision logic (no browser)."""

from __future__ import annotations

from bot.browser import name_from_title
from bot.config import Settings
from bot.invitations import NOT_A_CONNECTION_CARD, NOT_A_CONNECTION_LABEL, inviter_name
from bot.messaging import (
    Card,
    baseline_ready,
    pick_candidates,
    resolve_directions,
    same_person,
    same_text,
    thread_key,
)
from bot.replies import Message, build_system_prompt, build_user_prompt, fallback_reply, tidy
from bot.state import State

SETTINGS = Settings()


def card(index: int, name: str, snippet: str) -> Card:
    return Card(index=index, mode="label", name=name, snippet=snippet)


def raw(sender: str, text: str, mine=None, marked: bool = False) -> dict:
    return {"sender": sender, "text": text, "mine": mine, "marked": marked}


# --- invitations -------------------------------------------------------------
def test_inviter_name():
    assert inviter_name("Accept Jane Doe’s invitation", SETTINGS) == "Jane Doe"
    assert inviter_name("Accept Omar Haddad's invitation to connect", SETTINGS) == "Omar Haddad"
    assert inviter_name("Accept Accept Jane Doe’s invitation", SETTINGS) == "Jane Doe"
    assert inviter_name("Accept", SETTINGS) == "a new connection"
    assert inviter_name("Accept invitation from Jane Doe", SETTINGS) == "Jane Doe"
    # No name in the label: the first line of the card that is not a button.
    assert inviter_name("Accept", SETTINGS, "Jane Doe\nRecruiter at Acme\nIgnore\nAccept") == "Jane Doe"


def test_owner_name_from_the_profile_page_title():
    assert name_from_title("Jane Doe | LinkedIn") == "Jane Doe"
    assert name_from_title("(3) Jane Doe | LinkedIn") == "Jane Doe"
    assert name_from_title("(99+) Jane Doe | LinkedIn") == "Jane Doe"
    assert name_from_title("LinkedIn") == ""
    assert name_from_title("LinkedIn | LinkedIn") == ""


def test_only_connection_requests_are_accepted():
    assert not NOT_A_CONNECTION_LABEL.search("Accept Jane Doe’s invitation")
    assert not NOT_A_CONNECTION_LABEL.search("Accept Larry Page’s invitation to connect")
    assert NOT_A_CONNECTION_LABEL.search("Accept invitation to subscribe to Tech Weekly")
    assert NOT_A_CONNECTION_LABEL.search("Accept invitation to follow Acme Corp")
    assert NOT_A_CONNECTION_LABEL.search("Accept invitation to attend AI Summit")
    # A person whose headline mentions events or newsletters is still a person.
    assert not NOT_A_CONNECTION_CARD.search("Omar Haddad · Event Manager, newsletter editor")
    assert NOT_A_CONNECTION_CARD.search("Dana Roy invited you to follow Acme Corp")


# --- choosing conversations --------------------------------------------------
def test_baseline_remembers_everything_and_picks_nothing():
    state = State()
    cards = [card(0, "A", "hello"), card(1, "B", "You: hi")]

    assert pick_candidates(cards, state, SETTINGS) == []
    assert state.baselined
    assert state.seen == {"A": "hello", "B": "You: hi"}


def test_changed_conversations_above_the_first_unchanged_one_are_picked():
    state = State(baselined=True, seen={"B": "old", "C": "old"})
    cards = [card(0, "A", "new person"), card(1, "B", "changed"), card(2, "C", "old"), card(3, "D", "x")]

    picked = pick_candidates(cards, state, SETTINGS)

    # D was never seen, but it sits below an unchanged conversation, so it is
    # old history that simply was not on screen before: not picked.
    assert [c.name for c in picked] == ["A", "B"]


def test_own_last_message_is_not_a_candidate():
    state = State(baselined=True, seen={"A": "hello"})
    cards = [card(0, "A", "You: thanks!"), card(1, "B", "new")]

    assert [c.name for c in pick_candidates(cards, state, SETTINGS)] == ["B"]
    assert state.seen["A"] == "You: thanks!"


def test_baseline_waits_for_a_complete_list():
    state = State()
    assert not baseline_ready(True, [], state)  # empty: could still be loading
    assert not baseline_ready(True, [card(1, "B", "x")], state)  # top row not rendered yet
    assert baseline_ready(True, [], state)  # third look: believe it

    assert baseline_ready(True, [card(0, "A", "x")], State())  # complete list: at once
    never = State()
    assert not any(baseline_ready(False, [], never) for _ in range(5))  # page never loaded


def test_a_list_that_loads_late_is_a_baseline_not_new_mail():
    state = State(baselined=True)  # baseline was taken on an inbox that looked empty
    cards = [card(0, "A", "old"), card(1, "B", "old"), card(2, "C", "old")]

    assert pick_candidates(cards, state, SETTINGS) == []
    assert set(state.seen) == {"A", "B", "C"}

    one_new = State(baselined=True)  # a truly empty inbox getting its first message
    assert [c.name for c in pick_candidates([card(0, "A", "hi")], one_new, SETTINGS)] == ["A"]


# --- reading a thread --------------------------------------------------------
def test_directions_from_profile_ids():
    thread = [raw("Jane", "hi", mine=False), raw("Me", "hello", mine=True)]
    assert [m.incoming for m in resolve_directions(thread, "")] == [True, False]


def test_directions_from_the_marker():
    thread = [raw("Jane", "hi", marked=True), raw("Me", "hello")]
    assert [m.incoming for m in resolve_directions(thread, "")] == [True, False]


def test_directions_from_names_when_nothing_else_is_available():
    thread = [raw("Charbel Test", "hello"), raw("Jane Doe", "hi")]
    assert [m.incoming for m in resolve_directions(thread, "Charbel Test")] == [False, True]


def test_unknown_or_contradictory_direction_is_reported_not_guessed():
    assert resolve_directions([], "Charbel") is None
    assert resolve_directions([raw("Jane Doe", "hi")], "") is None  # no clue at all
    assert resolve_directions([raw("", "hi")], "Charbel") is None
    # The profile id says "incoming", the owner's name says "mine".
    assert resolve_directions([raw("Charbel", "hi", mine=False)], "Charbel") is None


def test_same_person():
    assert same_person("Jane  Doe", "jane doe")
    assert same_person("Jane Doe Status is online", "Jane Doe")
    assert not same_person("Jane Doe", "John Doe")
    assert not same_person("", "Jane Doe")


def test_thread_key():
    assert thread_key("https://www.linkedin.com/messaging/thread/2-abc==/", "Jane") == "2-abc=="
    assert thread_key("https://www.linkedin.com/messaging/compose/", "Jane") == "Jane"


# --- state -------------------------------------------------------------------
def test_state_round_trip(tmp_path):
    state = State.load(tmp_path / "state.json")
    state.baselined = True
    state.remember("Jane", "hi")
    state.record_reply("thread-1", "Hello Jane")
    state.record_accept("Jane")
    state.save()

    again = State.load(tmp_path / "state.json")
    assert again.baselined and again.seen == {"Jane": "hi"}
    assert again.has_replied("thread-1")
    assert again.last_reply == {"thread-1": "Hello Jane"}
    assert again.accepted[0]["name"] == "Jane"


def test_state_is_reset_when_another_account_signs_in(tmp_path):
    state = State(path=tmp_path / "state.json", baselined=True, seen={"Jane": "hi"})

    assert state.for_account("Jack Helou") is state  # first time: just remember who it is
    assert state.for_account("jack  helou") is state  # same account: nothing changes
    assert state.for_account("") is state  # name unreadable: keep what we have
    assert state.seen == {"Jane": "hi"} and state.owner_name == "jack  helou"

    other = state.for_account("Charbel Test")  # someone else: clean state, same file
    assert other is not state
    assert not other.baselined and other.seen == {} and other.replies == {}
    assert other.owner_name == "Charbel Test" and other.path == state.path


def test_reply_counts_only_cover_the_last_hour():
    state = State()
    state.record_reply("a", now=1_000.0)
    state.record_reply("a", now=4_000.0)
    state.record_reply("b", now=4_100.0)

    assert state.replies_last_hour("a", now=4_200.0) == 2
    assert state.replies_last_hour("a", now=4_700.0) == 1  # the first one is over an hour old
    assert state.replies_last_hour(now=4_700.0) == 2  # across all conversations


def test_failures_reset_once_remembered():
    state = State()
    assert state.note_failure("Jane") == 1
    assert state.note_failure("Jane") == 2
    state.remember("Jane", "hi")
    assert state.note_failure("Jane") == 1


# --- replies -----------------------------------------------------------------
def test_fallback_reply():
    assert fallback_reply("Jane Doe", disclose=False) == (
        "Hey Jane, thanks for the message! Bit tied up right now, I'll get back to you properly soon."
    )
    assert fallback_reply("", disclose=True).endswith("(This is an automatic reply.)")
    # The wording rotates, so the same sentence is not sent twice in a row.
    assert fallback_reply("Jane Doe", False, 1) != fallback_reply("Jane Doe", False, 2)


def test_prompts_carry_the_conversation_and_the_rules():
    messages = [Message("Jane Doe", "Are you open to work?", True), Message("Charbel", "Hi!", False)]
    user = build_user_prompt("Jane Doe", messages, "Charbel", first_reply=True)
    system = build_system_prompt("Charbel", "Final-year student.", disclose=False)

    assert "Jane Doe: Are you open to work?" in user
    assert "You: Hi!" in user
    assert "not replied in this conversation yet" in user
    assert "Final-year student." in system
    assert "first person" in system
    assert "automatic reply" not in system
    assert "automatic reply" in build_system_prompt("Charbel", "x", disclose=True)


def test_a_very_long_message_is_cut_in_the_prompt():
    user = build_user_prompt("Jane", [Message("Jane", "x" * 10_000, True)], "Charbel", True)
    assert len(user) < 4_000 and "the message continues" in user


def test_same_text_ignores_what_the_editor_may_restyle():
    assert same_text("Sounds good 👍", "sounds good")
    assert same_text("It's  fine.", "It’s fine")
    assert not same_text("Sounds good", "Sounds bad")
    assert same_text("👍", "😊")  # no letters on either side: both non-empty
    assert not same_text("", "👍")


def test_tidy():
    assert tidy('  "Hello there"  ') == "Hello there"
    assert len(tidy("x" * 5000)) == 1200
