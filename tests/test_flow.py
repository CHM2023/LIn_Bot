"""End-to-end tests: the real bot code drives a real (headless) Chrome against
the fake LinkedIn in `fake_linkedin.py`."""

from __future__ import annotations

import pytest

from bot.__main__ import run_cycle
from bot.browser import is_logged_in, lookup_owner_name
from bot.invitations import accept_pending
from bot.messaging import process_inbox, typing_check
from bot.replies import Responder
from bot.state import State
from tests.fake_linkedin import OWNER, FakeLinkedIn


def poll(s) -> int:
    return process_inbox(s.page, s.settings, s.state, s.responder)


# --- invitations -------------------------------------------------------------
def test_accepts_connection_requests_only(session):
    names = accept_pending(session.page, session.settings, session.state)

    assert names == ["Jane Doe", "Omar Haddad"]
    assert session.fake.accepted == ["Accept Jane Doe’s invitation", "Accept Omar Haddad’s invitation"]
    # The newsletter and the company-page invitations are left alone.
    assert [card["label"] for card in session.fake.invitations] == [
        "Accept invitation to subscribe to Tech Weekly",
        "Accept Acme Corp’s invitation",
    ]
    assert [entry["name"] for entry in session.state.accepted] == names


def test_no_pending_invitations_is_fine(session):
    session.fake.invitations = []
    assert accept_pending(session.page, session.settings, session.state) == []


def test_dry_run_accepts_nothing(session):
    session.with_settings(dry_run=True)
    names = accept_pending(session.page, session.settings, session.state)

    assert names == ["Jane Doe", "Omar Haddad"]  # reported...
    assert session.fake.accepted == []  # ...but not clicked
    assert session.state.accepted == []


# --- messages: the same story on every variant of the markup ------------------
MARKUP = {
    "classic": {},
    "hashed-class-names": {"hashed": True},
    "no-incoming-marker": {"markers": False},
    "no-thread-title": {"title": False},
    "no-data-attributes": {"ids": False},
    "hashed-and-no-title": {"hashed": True, "title": False},
}


@pytest.mark.parametrize("flags", MARKUP.values(), ids=MARKUP.keys())
def test_conversation_flow_on_every_markup(make_session, flags):
    # No "You:" shortcut here: every changed conversation is opened, so the
    # bot has to work out from the thread itself who wrote the last message.
    s = make_session(FakeLinkedIn(**flags), self_prefixes=("~never~",))

    assert poll(s) == 0  # baseline: "Old Contact" was waiting before the bot existed
    assert s.fake.sent == []

    s.fake.receive("Jane Doe", "Hi, thanks for connecting!")
    s.fake.receive("Omar Haddad", "Hello!")
    assert poll(s) == 2
    assert s.fake.sent == [
        ("Jane Doe", "Auto-reply to Jane Doe"),  # the one waiting longest goes first
        ("Omar Haddad", "Auto-reply to Omar Haddad"),
    ]
    assert poll(s) == 0  # its own replies are not answered

    s.fake.receive("Old Contact", "Sorry, answering from my phone.", mine=True)
    assert poll(s) == 0  # the owner wrote last: nothing to answer

    s.fake.receive("Jane Doe", "Are you there?")
    assert poll(s) == 1
    assert len(s.fake.sent) == 3
    first, _, follow_up = s.responder.calls
    assert first["owner"] == OWNER  # read from the page, not configured
    assert first["first_reply"] is True and follow_up["first_reply"] is False
    assert [(m.text, m.incoming) for m in follow_up["messages"]] == [
        ("Hi, thanks for connecting!", True),
        ("Auto-reply to Jane Doe", False),
        ("Are you there?", True),
    ]


# --- messages: individual behaviours -----------------------------------------
def test_first_poll_only_takes_a_baseline(session):
    assert poll(session) == 0
    assert poll(session) == 0
    assert session.fake.sent == []
    assert session.state.baselined


def test_answers_a_new_message_exactly_once(session):
    poll(session)
    session.fake.receive("Old Contact", "Hello again, any news?")

    assert poll(session) == 1
    assert session.fake.sent == [("Old Contact", "Auto-reply to Old Contact")]
    assert poll(session) == 0
    assert poll(session) == 0
    assert len(session.fake.sent) == 1


def test_does_not_answer_when_the_owner_wrote_last(session):
    poll(session)
    session.fake.receive("Old Contact", "Sorry for the delay, yes I am.", mine=True)

    assert poll(session) == 0
    assert session.fake.sent == []


def test_conversation_without_a_message_box_is_skipped(session):
    poll(session)
    session.fake.receive("LinkedIn Offers", "Try Premium for free", locked=True)
    session.fake.receive("Jane Doe", "Hi!")

    for _ in range(3):
        poll(session)
    assert session.fake.sent == [("Jane Doe", "Auto-reply to Jane Doe")]
    assert [call["contact"] for call in session.responder.calls] == ["Jane Doe"]


def test_reply_limit_per_conversation(session):
    session.with_settings(max_replies_per_thread_per_hour=1)
    poll(session)
    session.fake.receive("Jane Doe", "Hi!")
    poll(session)
    session.fake.receive("Jane Doe", "Hi again!")

    assert poll(session) == 0
    assert len(session.fake.sent) == 1


def test_hourly_limit_stops_all_replies(session):
    session.with_settings(max_replies_per_hour=1)
    poll(session)
    session.fake.receive("Jane Doe", "Hi!")
    poll(session)
    session.fake.receive("Omar Haddad", "Hello!")

    assert poll(session) == 0
    assert len(session.fake.sent) == 1


def test_multi_line_reply_is_sent_as_one_message(session):
    session.responder.text = "Thanks for reaching out.\nCharbel will reply soon."
    poll(session)
    session.fake.receive("Jane Doe", "Hi!")
    poll(session)

    assert session.fake.sent == [("Jane Doe", "Thanks for reaching out.\nCharbel will reply soon.")]


def test_dry_run_sends_nothing(session):
    session.with_settings(dry_run=True)
    poll(session)
    session.fake.receive("Jane Doe", "Hi!")

    assert poll(session) == 0
    assert session.fake.sent == []
    assert len(session.responder.calls) == 1  # the reply was drafted and logged


def test_empty_inbox_then_first_ever_message(make_session):
    fake = FakeLinkedIn()
    fake.threads = []
    s = make_session(fake)

    for _ in range(3):  # an empty inbox must be seen several times to be believed
        assert poll(s) == 0
    assert s.state.baselined
    fake.receive("Jane Doe", "Hi!")
    assert poll(s) == 1


def test_never_answers_itself_even_when_it_misreads_who_wrote_what(make_session):
    # Worst case for attribution: sender links do not carry the profile id,
    # there is no CSS marker and no owner name, so every message looks
    # incoming, the bot's own included. It must still reply only once.
    fake = FakeLinkedIn(hashed=True, vanity=True, owner_photo=False)
    s = make_session(fake, self_prefixes=("~never~",))
    poll(s)
    fake.receive("Jane Doe", "Hi!")

    for _ in range(4):
        poll(s)
    assert fake.sent == [("Jane Doe", "Auto-reply to Jane Doe")]


def test_conflicting_clues_mean_no_reply(make_session):
    # The CSS marker says "incoming" for the contact only, but the sender
    # links say everything is incoming. On the bot's own message the clues
    # disagree, and it must not act on a guess.
    fake = FakeLinkedIn(vanity=True)
    s = make_session(fake, self_prefixes=("~never~",))
    poll(s)
    fake.receive("Old Contact", "Answering from my phone.", mine=True)

    for _ in range(4):
        poll(s)
    assert fake.sent == []


def test_state_survives_a_restart(session, tmp_path):
    session.state = State.load(tmp_path / "state.json")
    poll(session)
    session.fake.receive("Jane Doe", "Hi!")

    session.state = State.load(tmp_path / "state.json")  # "restart" before the next poll
    assert poll(session) == 1
    session.state = State.load(tmp_path / "state.json")
    assert poll(session) == 0
    assert len(session.fake.sent) == 1


def test_fallback_reply_without_an_api_key(session, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    session.responder = Responder(session.settings)
    poll(session)
    session.fake.receive("Jane Doe", "Hi!")
    poll(session)

    assert session.fake.sent == [
        ("Jane Doe", "Hi Jane, thanks for your message! This is an automated reply. "
                     "Charbel will get back to you personally soon.")
    ]


# --- the whole cycle ---------------------------------------------------------
def test_full_cycle_accepts_then_answers(session):
    run_cycle(session.context, session.page, session.settings, session.state, session.responder)
    session.fake.receive("Jane Doe", "Thanks for accepting! Quick question for you.")
    run_cycle(session.context, session.page, session.settings, session.state, session.responder)

    assert len(session.fake.accepted) == 2
    assert session.fake.sent == [("Jane Doe", "Auto-reply to Jane Doe")]


def test_owner_name_falls_back_to_the_profile_page_title(make_session):
    with_photo = make_session()
    with_photo.page.goto("https://www.linkedin.com/feed/")
    assert lookup_owner_name(with_photo.page, with_photo.settings) == OWNER
    assert "/feed/" in with_photo.page.url  # found in the top bar: no navigation needed

    without = make_session(FakeLinkedIn(owner_photo=False))
    without.page.goto("https://www.linkedin.com/feed/")
    assert lookup_owner_name(without.page, without.settings) == OWNER


def test_typing_check_types_and_erases_without_sending(session):
    poll(session)
    session.page.goto("https://www.linkedin.com/messaging/")  # opens the top conversation
    session.page.wait_for_timeout(400)

    assert typing_check(session.page, session.settings) == {
        "box": True,
        "send_button": True,
        "ready_to_send_after": "insert",
        "erased": True,
    }
    assert session.fake.sent == []


def test_login_detection(make_session):
    signed_in = make_session()
    signed_in.page.goto("https://www.linkedin.com/feed/")
    assert is_logged_in(signed_in.context, signed_in.page, signed_in.settings)

    fake = FakeLinkedIn()
    fake.logged_in = False
    signed_out = make_session(fake)
    signed_out.page.goto("https://www.linkedin.com/feed/")
    assert not is_logged_in(signed_out.context, signed_out.page, signed_out.settings)
