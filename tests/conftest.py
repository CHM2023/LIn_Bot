from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext, Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.browser import first_page, open_context  # noqa: E402
from bot.config import Settings  # noqa: E402
from bot.state import State  # noqa: E402
from tests.fake_linkedin import FakeLinkedIn  # noqa: E402


class StubResponder:
    """Stands in for the model: records what it was asked, returns a fixed text."""

    def __init__(self, text: str | None = None):
        self.text = text
        self.calls: list[dict] = []

    def reply(self, contact, messages, owner="", first_reply=True):
        self.calls.append(
            {"contact": contact, "messages": messages, "owner": owner, "first_reply": first_reply}
        )
        return self.text or f"Auto-reply to {contact}"


@dataclass
class Session:
    settings: Settings
    fake: FakeLinkedIn
    context: BrowserContext
    page: Page
    state: State = field(default_factory=State)
    responder: StubResponder = field(default_factory=StubResponder)

    def with_settings(self, **changes) -> "Session":
        self.settings = replace(self.settings, **changes)
        return self


@pytest.fixture
def make_session(tmp_path):
    """Start a headless Chrome wired to a FakeLinkedIn. Usage: make_session(fake)."""
    opened = []
    playwright = sync_playwright().start()

    def _make(fake: FakeLinkedIn | None = None, **settings_overrides) -> Session:
        settings = Settings(
            profile_dir=tmp_path / f"profile-{len(opened)}",
            data_dir=tmp_path / "data",
            persona_file=tmp_path / "no-persona.md",
            headless=True,
            pace=0,
            typing_delay_ms=0,
            render_wait_ms=500,
            **settings_overrides,
        )
        fake = fake or FakeLinkedIn()
        context = open_context(playwright, settings)
        fake.install(context)
        opened.append(context)
        return Session(settings, fake, context, first_page(context))

    yield _make
    for context in opened:
        context.close()
    playwright.stop()


@pytest.fixture
def session(make_session) -> Session:
    return make_session()
