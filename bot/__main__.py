"""Command line entry point.

    python -m bot login     sign in once (opens Chrome)
    python -m bot run       run the bot until Ctrl+C
    python -m bot doctor    check that the bot can still read LinkedIn's pages
"""

from __future__ import annotations

import argparse
import logging
import random
import shutil
import sys
import time

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from . import dom
from .browser import (
    ensure_logged_in,
    first_match,
    first_page,
    goto,
    is_logged_in,
    lookup_owner_name,
    open_context,
)
from .config import Settings, load_settings
from .invitations import accept_pending, find_accept_buttons
from .messaging import (
    list_conversations,
    message_box,
    open_conversation,
    process_inbox,
    read_thread,
    resolve_directions,
    same_name,
    typing_check,
)
from .replies import Responder
from .state import State

log = logging.getLogger("bot")


def setup_logging(settings: Settings, verbose: bool) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(settings.log_file, encoding="utf-8"),
        ],
    )


def run_cycle(
    context: BrowserContext,
    page: Page,
    settings: Settings,
    state: State,
    responder: Responder,
    check_invitations: bool = True,
) -> bool:
    """One poll: accept invitations, then answer new messages.

    Returns True when something happened (an invitation accepted or a reply
    sent), which makes the loop poll faster for a while.
    """
    accepted = accept_pending(page, settings, state) if check_invitations else []
    state.save()
    sent = process_inbox(page, settings, state, responder)
    state.save()
    if not is_logged_in(context, page, settings):
        log.warning("The LinkedIn session ended; a new sign-in is needed.")
        ensure_logged_in(context, page, settings)
    return bool(accepted) or sent > 0


def bind_to_account(state: State, account: str, settings: Settings) -> State:
    """Make sure the saved state belongs to the account that is signed in."""
    bound = state.for_account(account)
    if bound is not state:
        log.warning(
            "A different LinkedIn account is signed in than last time (%s): starting from "
            "a clean state. The first poll will only take a baseline.",
            account,
        )
    elif not (account or state.owner_name or settings.owner_name):
        log.warning("Could not read the account owner's name; set OWNER_NAME in .env.")
    bound.save()
    return bound


def cmd_run(settings: Settings, once: bool) -> int:
    # A dry run keeps its notes in memory, so it never affects the real state.
    state = State.load(None if settings.dry_run else settings.state_file)
    responder = Responder(settings)
    with sync_playwright() as playwright:
        context = open_context(playwright, settings)
        try:
            page = first_page(context)
            ensure_logged_in(context, page, settings)
            state = bind_to_account(state, lookup_owner_name(page, settings), settings)
            log.info(
                "Bot is running%s. Press Ctrl+C to stop.",
                " in DRY-RUN mode (nothing is accepted or sent)" if settings.dry_run else "",
            )
            last_activity = 0.0
            cycle = 0
            while True:
                cycle += 1
                # During a conversation the bot polls fast and looks at the
                # invitations page only every third poll; when nothing has
                # happened for a while it slows down again.
                active = time.time() - last_activity < settings.active_minutes * 60
                try:
                    if run_cycle(
                        context,
                        page,
                        settings,
                        state,
                        responder,
                        check_invitations=not active or cycle % 3 == 0,
                    ):
                        last_activity = time.time()
                        active = True
                except PlaywrightError as error:
                    log.warning("Poll failed: %s", str(error).splitlines()[0])
                    if page.is_closed():
                        try:
                            page = context.new_page()
                        except PlaywrightError:
                            log.error("The browser was closed; stopping.")
                            return 1
                if once:
                    return 0
                delay = settings.active_poll_seconds if active else settings.poll_seconds
                time.sleep(delay * random.uniform(0.8, 1.3))
        except KeyboardInterrupt:
            log.info("Stopping.")
            return 0
        finally:
            state.save()
            try:
                context.close()
            except PlaywrightError:
                pass


def cmd_login(settings: Settings, switch: bool = False) -> int:
    if switch:
        # Forget the saved browser session so another account can sign in.
        shutil.rmtree(settings.profile_dir, ignore_errors=True)
    with sync_playwright() as playwright:
        context = open_context(playwright, settings)
        try:
            page = first_page(context)
            ensure_logged_in(context, page, settings)
            account = lookup_owner_name(page, settings)
            bind_to_account(State.load(settings.state_file), account, settings)
            log.info("Signed in as: %s", account or "(name not detected)")
            return 0
        finally:
            context.close()


def _count(page: Page, selectors: list[str]) -> str:
    return ", ".join(f"{page.locator(selector).count()} x {selector}" for selector in selectors)


def cmd_doctor(
    settings: Settings, show_text: bool, save_pages: bool, type_test: bool = False
) -> int:
    """Report which selectors match. Prints counts only, unless --show-text."""

    def shown(text: str) -> str:
        return repr(text) if show_text else f"<{len(text)} chars>"

    def snapshot(page: Page, name: str) -> None:
        if save_pages:
            settings.debug_dir.mkdir(parents=True, exist_ok=True)
            (settings.debug_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(settings.debug_dir / f"{name}.png"))
            print(f"  saved {settings.debug_dir / name}.html and .png (private: do not share)")

    with sync_playwright() as playwright:
        context = open_context(playwright, settings)
        try:
            page = first_page(context)
            ensure_logged_in(context, page, settings)
            owner = settings.owner_name or lookup_owner_name(page, settings)
            print(f"Signed in: yes. Owner name: {shown(owner) if owner else 'NOT DETECTED (set OWNER_NAME in .env)'}")

            print("\nInvitations page")
            goto(page, settings, dom.INVITATIONS_PATH)
            page.wait_for_timeout(settings.render_wait_ms)
            buttons = find_accept_buttons(page, settings)
            print(f"  connection requests with an Accept button: {len(buttons)}")
            for _, name in buttons:
                print(f"    from: {shown(name)}")
            snapshot(page, "invitations")

            print("\nMessaging page")
            goto(page, settings, dom.MESSAGING_PATH)
            page_loaded, cards = list_conversations(page, settings)
            print(f"  page recognised: {page_loaded}")
            print(
                "  conversation rows: "
                + _count(page, [dom.CONVERSATION_ROWS, dom.CONVERSATION_ROWS_BY_CLASS])
            )
            print(f"  rows read (with a name): {len(cards)}")
            for card in cards[:5]:
                print(
                    f"    #{card.index} found by {card.mode}: name={shown(card.name)} "
                    f"preview={shown(card.snippet)}"
                )
            snapshot(page, "messaging-list")

            if cards:
                # Opening a conversation marks it as read, like a person would.
                print("\nTop conversation (opened to check that threads can be read)")
                raw = open_conversation(page, cards[0], settings)
                if raw is None:
                    raw = read_thread(page)
                    print("  NOT VERIFIED as the clicked conversation; what is on screen:")
                messages = resolve_directions(raw["messages"], owner)
                count = len(raw["messages"])
                print(f"  thread title: {shown(raw['title'])}; messages read: {count}")
                print(f"    with a sender name: {sum(1 for m in raw['messages'] if m['sender'])}/{count}")
                print(f"    attributed by profile id: {sum(1 for m in raw['messages'] if m['mine'] is not None)}/{count}")
                print(f"    carrying the 'other person' CSS marker: {sum(1 for m in raw['messages'] if m['marked'])}/{count}")
                if raw["messages"]:
                    # New messages are noticed through the preview text in the
                    # list, so it has to follow the thread's last message.
                    last_text = " ".join(raw["messages"][-1]["text"].split()).casefold()
                    preview = " ".join(cards[0].snippet.split()).casefold()
                    follows = bool(last_text) and (last_text[:12] in preview or preview in last_text)
                    print(f"    list preview reflects the last message: {follows}")
                    if same_name(cards[0].name, cards[0].snippet):
                        print("    WARNING: the preview read from the list is just the name")
                own = [m["sender"] for m in raw["messages"] if m["mine"] is True and m["sender"]]
                if own and owner:
                    agree = all(same_name(sender, owner) for sender in own)
                    print(f"    owner name matches the name on your own messages: {agree}")
                if messages is None:
                    print("    last message: author UNKNOWN (the bot would not reply)")
                else:
                    who = "the other person" if messages[-1].incoming else "you"
                    print(f"    last message written by: {who}")
                box = message_box(page)
                print(f"  message box found: {box is not None}")
                if box is not None:
                    form = box.locator("xpath=ancestor::form[1]")
                    scope = form if form.count() else page.locator("main")
                    print(f"  send button found: {first_match(scope, dom.SEND_BUTTON) is not None}")
                snapshot(page, "messaging-thread")
                if type_test:
                    print("\nTyping test (types 'test' in the message box, then erases it; sends nothing)")
                    for key, value in typing_check(page, settings).items():
                        print(f"  {key}: {value}")
            return 0
        finally:
            context.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bot", description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", action="store_true", help="log every decision")
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser("login", help="sign in once; the session is saved locally")
    login.add_argument(
        "--switch",
        action="store_true",
        help="forget the saved session first, to sign in with another account",
    )

    run = commands.add_parser("run", help="accept invitations and answer messages")
    run.add_argument("--dry-run", action="store_true", help="log what would happen, do nothing")
    run.add_argument("--once", action="store_true", help="do a single poll and exit")
    run.add_argument("--headless", action="store_true", help="no visible window")

    doctor = commands.add_parser("doctor", help="check the page selectors")
    doctor.add_argument("--show-text", action="store_true", help="print names and message text")
    doctor.add_argument("--save-pages", action="store_true", help="save HTML + screenshots to data/debug")
    doctor.add_argument(
        "--type-test",
        action="store_true",
        help="also type a word in the top conversation's message box and erase it (sends nothing)",
    )

    args = parser.parse_args(argv)
    # Names and messages can contain any script; never let the console choke on them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.command == "run":
        settings = load_settings(
            dry_run=True if args.dry_run else None, headless=True if args.headless else None
        )
    else:
        settings = load_settings(headless=False)
    setup_logging(settings, args.verbose)

    if args.command == "login":
        return cmd_login(settings, args.switch)
    if args.command == "doctor":
        return cmd_doctor(settings, args.show_text, args.save_pages, args.type_test)
    return cmd_run(settings, args.once)


if __name__ == "__main__":
    sys.exit(main())
