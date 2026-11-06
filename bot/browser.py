"""Browser session: a real Chrome window with its own persistent profile.

The account owner signs in by hand once. Chrome keeps the session inside
`.session/` (git-ignored), so the bot never sees or stores the password.
"""

from __future__ import annotations

import logging
import random
import re
import time

from playwright.sync_api import BrowserContext, Locator, Page, Playwright
from playwright.sync_api import Error as PlaywrightError

from . import dom
from .config import Settings

log = logging.getLogger("bot.browser")

LOGGED_OUT_MARKERS = ("/login", "/uas/", "/checkpoint", "/authwall", "/signup")


def open_context(playwright: Playwright, settings: Settings) -> BrowserContext:
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(settings.profile_dir),
        channel=settings.browser_channel or None,
        headless=settings.headless,
        # A fixed desktop size keeps LinkedIn on its two-pane messaging layout.
        viewport={"width": 1400, "height": 900},
    )


def first_page(context: BrowserContext) -> Page:
    return context.pages[0] if context.pages else context.new_page()


def goto(page: Page, settings: Settings, path: str) -> None:
    page.goto(settings.base_url + path, wait_until="domcontentloaded", timeout=45_000)


def pause(page: Page, settings: Settings, low: float, high: float) -> None:
    """Wait a random, human-like moment (seconds), scaled by `settings.pace`."""
    seconds = random.uniform(low, high) * settings.pace
    if seconds > 0:
        page.wait_for_timeout(seconds * 1000)


def first_match(root: Page | Locator, candidates: list[str]) -> Locator | None:
    """Return the first candidate selector that exists under `root`."""
    for selector in candidates:
        found = root.locator(selector)
        if found.count() > 0:
            return found.first
    return None


def is_logged_in(context: BrowserContext, page: Page, settings: Settings) -> bool:
    # `li_at` is LinkedIn's session cookie. We only check that it exists.
    has_session = any(c["name"] == "li_at" for c in context.cookies(settings.base_url))
    return has_session and not any(marker in page.url for marker in LOGGED_OUT_MARKERS)


def ensure_logged_in(
    context: BrowserContext, page: Page, settings: Settings, wait_minutes: float = 10
) -> None:
    """Make sure there is a live session; if not, let the human sign in."""
    goto(page, settings, dom.FEED_PATH)
    if is_logged_in(context, page, settings):
        return
    if settings.headless:
        raise RuntimeError(
            "Not signed in to LinkedIn. Run `python -m bot login` once (it opens a window)."
        )
    if not any(marker in page.url for marker in LOGGED_OUT_MARKERS):
        goto(page, settings, dom.LOGIN_PATH)
    log.info(
        "Sign in to LinkedIn in the Chrome window (waiting up to %d minutes)...", wait_minutes
    )
    deadline = time.monotonic() + wait_minutes * 60
    while time.monotonic() < deadline:
        page.wait_for_timeout(1500)
        if is_logged_in(context, page, settings):
            log.info("Signed in. The session is saved for next time.")
            return
    raise RuntimeError("Timed out waiting for the LinkedIn sign-in.")


def detect_owner_name(page: Page) -> str:
    """The account owner's name, read from the profile photo in the top bar.

    Cheap (no navigation), but only present on LinkedIn's older page layout.
    """
    photo = first_match(page, dom.OWNER_PHOTO)
    if photo is None:
        return ""
    alt = (photo.get_attribute("alt") or "").strip()
    for prefix in ("Photo of ", "Photo de "):
        if alt.startswith(prefix):
            alt = alt[len(prefix) :]
    return alt.strip()


def name_from_title(title: str) -> str:
    """'(3) Jane Doe | LinkedIn' -> 'Jane Doe'."""
    title = re.sub(r"^\(\d+\+?\)\s*", "", title).strip()
    if "|" not in title:
        return ""
    name = title.split("|")[0].strip()
    return "" if name.casefold() == "linkedin" else name


def lookup_owner_name(page: Page, settings: Settings) -> str:
    """The account owner's name: from the top bar if it is there, otherwise
    from the title of their own profile page, which reads "Name | LinkedIn".
    Done once at start-up; the result is kept in the state file.
    """
    name = detect_owner_name(page)
    if name:
        return name
    try:
        goto(page, settings, dom.OWN_PROFILE_PATH)  # redirects to the member's own profile
        for _ in range(20):
            name = name_from_title(page.title())
            if name:
                return name
            page.wait_for_timeout(300)
    except PlaywrightError:
        pass
    return ""
