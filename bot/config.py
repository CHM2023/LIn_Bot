"""Runtime settings.

Everything comes from environment variables (or a local `.env` file), so no
account detail or secret ever lives in the source code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _number(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def _words(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return tuple(word.strip() for word in raw.split(",") if word.strip())


@dataclass(frozen=True)
class Settings:
    # Where things live
    base_url: str = "https://www.linkedin.com"
    profile_dir: Path = ROOT / ".session" / "chrome-profile"
    data_dir: Path = ROOT / "data"
    persona_file: Path = ROOT / "persona.md"

    # Browser
    browser_channel: str = "chrome"  # the installed Chrome; "" = Playwright's Chromium
    headless: bool = False

    # Behaviour
    dry_run: bool = False  # look and log, but never click Accept or Send
    poll_seconds: float = 20
    pace: float = 1.0  # multiplier on the human-like pauses (tests use 0)
    typing_delay_ms: float = 15
    render_wait_ms: float = 6000  # how long to wait for page content to appear
    disclose_automation: bool = True

    # Safety limits: a bug or a bot on the other side must not flood anyone.
    max_conversations: int = 15
    max_accepts_per_cycle: int = 20
    max_replies_per_cycle: int = 5
    max_replies_per_hour: int = 40
    max_replies_per_thread_per_hour: int = 12
    history_messages: int = 12

    # Identity and wording
    owner_name: str = ""  # auto-detected from the page when empty
    accept_words: tuple[str, ...] = ("Accept", "Accepter")
    self_prefixes: tuple[str, ...] = ("You:", "Vous :", "Vous:")

    # Reply generation
    model: str = "claude-opus-5-5"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "bot.log"

    @property
    def debug_dir(self) -> Path:
        return self.data_dir / "debug"


def load_settings(**overrides) -> Settings:
    """Read `.env` + environment; keyword arguments (CLI flags) win over both."""
    load_dotenv(ROOT / ".env")
    defaults = Settings()
    settings = Settings(
        base_url=os.getenv("LINKEDIN_BASE_URL", defaults.base_url).rstrip("/"),
        browser_channel=os.getenv("BROWSER_CHANNEL", defaults.browser_channel).strip(),
        headless=_flag("HEADLESS", defaults.headless),
        dry_run=_flag("DRY_RUN", defaults.dry_run),
        poll_seconds=_number("POLL_SECONDS", defaults.poll_seconds),
        disclose_automation=_flag("DISCLOSE_AUTOMATION", defaults.disclose_automation),
        max_replies_per_hour=int(_number("MAX_REPLIES_PER_HOUR", defaults.max_replies_per_hour)),
        max_replies_per_thread_per_hour=int(
            _number("MAX_REPLIES_PER_THREAD_PER_HOUR", defaults.max_replies_per_thread_per_hour)
        ),
        owner_name=os.getenv("OWNER_NAME", defaults.owner_name).strip(),
        accept_words=_words("ACCEPT_WORDS", defaults.accept_words),
        self_prefixes=_words("SELF_PREFIXES", defaults.self_prefixes),
        model=os.getenv("ANTHROPIC_MODEL", defaults.model).strip() or defaults.model,
    )
    return replace(settings, **{k: v for k, v in overrides.items() if v is not None})
