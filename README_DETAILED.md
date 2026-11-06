# LinkedIn auto-responder bot: detailed documentation

> **Note.** This file was written on 6 November 2026, **before the timed test was started**. It describes the bot as it was at that point (branch `before-start`). Changes made during the test are on `after-start` and may not be reflected here.

For installation and everyday use, see [README.md](README.md). This document explains how the bot is built and why.

## Contents

1. [What the bot does](#1-what-the-bot-does)
2. [Libraries used](#2-libraries-used)
3. [Project layout](#3-project-layout)
4. [One poll, step by step](#4-one-poll-step-by-step)
5. [File by file](#5-file-by-file)
6. [The hard problems and how they are solved](#6-the-hard-problems-and-how-they-are-solved)
7. [What the bot relies on in LinkedIn's pages](#7-what-the-bot-relies-on-in-linkedins-pages)
8. [How a reply is written](#8-how-a-reply-is-written)
9. [The state file](#9-the-state-file)
10. [Configuration reference](#10-configuration-reference)
11. [Tests](#11-tests)
12. [What was checked on the real site](#12-what-was-checked-on-the-real-site)
13. [Limitations and known gaps](#13-limitations-and-known-gaps)
14. [Acknowledgement](#14-acknowledgement)

## 1. What the bot does

It runs on the owner's own LinkedIn account and does two things without help:

- **Accepts connection requests.** Only requests from people; invitations to newsletters, company pages and events are left alone.
- **Answers direct messages.** Only messages that arrive while it is running. The answer is written from the conversation so far and from `persona.md`, a short description of the account owner.

LinkedIn has no public API for either task on a personal account. The bot therefore uses the website itself, in a real Chrome window, the way a person would.

## 2. Libraries used

| Library | Version tested | Used for |
|---|---|---|
| Python | 3.10 | The language. Only standard syntax; no framework. |
| [Playwright](https://playwright.dev/python/) | 1.64 | Drives Chrome: opens pages, finds elements, clicks, types. |
| [anthropic](https://github.com/anthropics/anthropic-sdk-python) | 2.6 | Calls Claude to write the replies. |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.2 | Loads settings and the API key from `.env`. |
| [pytest](https://docs.pytest.org/) | 9.2 | Runs the tests. |

From the standard library: `argparse` (command line), `logging`, `dataclasses`, `json`, `re`, `pathlib`, `shutil`.

**Why Playwright.**

- It can drive the Chrome already installed on the computer (`channel="chrome"`) with a *persistent profile*. The owner signs in once by hand and Chrome remembers the session, so the code never handles the password.
- It finds elements by role and accessible name (`get_by_role("button", name=...)`), which keeps working when LinkedIn renames its CSS classes.
- It waits for elements automatically before clicking.
- It can intercept network requests, which is what lets the tests replace LinkedIn with a local fake.

**Why not LinkedIn's internal web API.** The site talks to private endpoints whose identifiers change, and calling them means handling session cookies and anti-forgery tokens in code. Clicking through the page is slower but uses nothing a normal visitor does not use.

**Why Claude for the replies.** The testers send real messages, so a fixed answer is not enough. The model call is one function; if it fails, the bot falls back to a fixed message instead of staying silent.

## 3. Project layout

```
bot/
  __main__.py      command line (login, run, doctor) and the polling loop
  config.py        settings, read from .env
  browser.py       Chrome session, sign-in check, owner's name
  invitations.py   accept connection requests
  messaging.py     find new messages, read threads, send replies
  replies.py       turn a conversation into a reply (Claude, or a fallback)
  state.py         the bot's memory, saved in data/state.json
  dom.py           everything the bot knows about LinkedIn's markup
tests/
  fake_linkedin.py a small fake LinkedIn used by the tests
  conftest.py      test setup (headless Chrome wired to the fake)
  test_flow.py     end-to-end tests
  test_units.py    tests of the decision logic
persona.md         who the account owner is (sample data, see section 13)
.env.example       all settings, with comments
requirements.txt   the four libraries above
```

Not in the repository (git-ignored): `.env` (API key), `.session/` (Chrome profile with the LinkedIn session), `data/` (state, log, page snapshots).

## 4. One poll, step by step

`python -m bot run` signs in if needed, then repeats this every `POLL_SECONDS` (20 by default, with a random variation so the rhythm is not mechanical):

```
1. Invitations
   open /mynetwork/invitation-manager/received/
   for each "Accept" button that belongs to a person:
       click it, wait until the invitation is gone, note the name

2. Messages
   open /messaging/compose/            (shows the list, opens no conversation)
   read each row: name + preview text
   first poll ever?  -> remember all rows, answer nothing ("baseline"), stop
   walk the rows from the top:
       row unchanged since last poll?   -> stop (everything below is older)
       preview starts with "You:"?      -> our own message, remember, next
       otherwise                         -> this conversation needs a look
   for each conversation that needs a look (oldest first):
       click the row, wait for the right thread to be on screen
       read the messages and who wrote each
       last message from the other person?
           no  -> nothing to do
           yes -> ask Claude for a reply, type it, click Send,
                  read the thread back to confirm it is there

3. Save the state, sleep, repeat
```

## 5. File by file

### `bot/config.py`

A frozen `Settings` dataclass holding every setting with its default. `load_settings()` reads `.env` and the environment, then applies command-line flags on top. Nothing about an account or a key is written in the code.

### `bot/browser.py`

- `open_context()` starts Chrome with a profile folder inside `.session/`, at a fixed desktop window size so LinkedIn keeps its two-pane messaging layout.
- `is_logged_in()` checks that LinkedIn's session cookie exists and that the page is not a login or security-check page. It only checks that the cookie exists; it never reads or stores its value.
- `ensure_logged_in()` opens the feed. If there is no session it opens the login page and waits up to ten minutes for the human to sign in (this also covers two-step verification and captchas).
- `lookup_owner_name()` finds the account owner's name: from the top bar where LinkedIn still shows it, otherwise from the title of the owner's own profile page (`/in/me/`), which reads "Name | LinkedIn".
- `pause()` waits a random, human-like moment. `first_match()` returns the first of several candidate selectors that exists.

### `bot/invitations.py`

- `find_accept_buttons()` finds buttons whose accessible name contains "Accept". A button is dropped if its label mentions *subscribe*, *follow*, *attend* or *newsletter*, or if its invitation card says "invited you to follow / subscribe / attend". What remains are connection requests.
- `accept_pending()` clicks them one at a time and waits for the count to go down after each click. If a click has no effect it stops instead of clicking again. At most 20 per poll.
- `inviter_name()` extracts the person's name for the log.

### `bot/messaging.py`

The largest file. In the order the code runs:

- `list_conversations()` reads the rows of the conversation list.
- `baseline_ready()` and `pick_candidates()` decide which conversations changed.
- `open_conversation()` clicks a row and returns the thread only once it is certain the right one is on screen.
- `resolve_directions()` decides, for each message, whether the other person wrote it.
- `message_box()`, `_write()`, `send_reply()` type and send.
- `confirm_sent()` reads the thread back after sending.
- `answer()` handles one conversation from start to finish; `process_inbox()` is one full pass over the inbox.

### `bot/replies.py`

- `Responder.reply()` returns the text to send. It calls Claude when an API key is configured and falls back to `fallback_reply()` otherwise, or when anything goes wrong.
- `build_system_prompt()` and `build_user_prompt()` build the two parts of the request (section 8).
- `tidy()` removes stray quotation marks and caps the length at 1200 characters.

### `bot/state.py`

A `State` dataclass saved as JSON (section 9). It is written to a temporary file first and then renamed, so a crash cannot leave a half-written file. `for_account()` returns a clean state when a different LinkedIn account is signed in than last time.

### `bot/dom.py`

All selectors, and the JavaScript snippets that read a page. These snippets only read text and attributes; every decision is taken in Python. If LinkedIn changes its markup, this is the only file to edit.

### `bot/__main__.py`

The command line.

- `run` is the loop of section 4. A failed poll is logged and the loop continues; if the browser window is closed the bot stops.
- `login` signs in and records which account it is. `--switch` forgets the saved session first.
- `doctor` opens the invitations and messaging pages and reports which anchors match. It prints counts only, never names or message text, unless `--show-text` is given. `--type-test` additionally types one word into the message box, checks that Send becomes clickable, and erases it. Nothing is sent.

## 6. The hard problems and how they are solved

### Not answering old messages

A bot that, on its first start, replied to every unanswered conversation in the inbox would be a disaster on a real account.

- The first poll is a **baseline**: every row is remembered and nothing is answered.
- The baseline is only taken when the list is clearly complete. An empty or half-loaded list must be seen on three polls in a row before it is believed, because a list that loads late would otherwise make every old conversation look new.
- If many rows appear at once after an "empty" baseline, that is treated as the list finishing to load, not as new mail.
- The state belongs to one account. If another account signs in, the state is reset and a new baseline is taken.

### Finding new messages without opening everything

Opening a conversation marks it as read, so the bot must not open them all. LinkedIn sorts conversations by most recent activity, so anything new is at the top. The bot walks down from the top and stops at the first row whose preview text has not changed. Only the changed rows above it are opened.

The preview's timestamp is deliberately not part of the comparison: it changes on its own (from "10:42" to a date at midnight) and would cause false alarms.

### Replying in the right conversation

After a click, the previous thread stays on screen for a moment. Typing then would send the answer to the wrong person. `open_conversation()` returns the thread only when all of this holds:

- the name above the thread matches the clicked row;
- a name printed next to the messages matches the clicked row;
- two consecutive reads of the thread are identical (it has stopped loading).

If this cannot be confirmed, the bot does nothing and retries on the next poll. After three failures it gives up on that change and logs a warning.

### Knowing who wrote the last message

Replying to its own message would start a loop. Three independent clues say whether a message is incoming:

1. **Profile id.** Every message element carries an id (`data-event-urn`) that starts with the signed-in member's profile id. Each group of messages is headed by a link to its sender's profile. If the two ids are equal, the message is ours.
2. **CSS marker.** LinkedIn puts the class `msg-s-event-listitem--other` on the other person's messages.
3. **Name.** The sender's name compared with the account owner's name.

For the last message, the clues that are available must all agree. With no clue, or with two that disagree, the bot does not reply.

A fourth, separate check: the last message's text is compared with the text of the bot's own last reply in that conversation. Even if every clue above were wrong, the bot could not answer itself.

### Typing into LinkedIn's editor

The message box is a rich-text editor, not a plain input.

- The bot requires exactly one visible message box. Sponsored and closed conversations have none and are skipped.
- Any leftover draft is erased first.
- The text is inserted in one go, line by line, with `Shift+Enter` between lines (plain `Enter` could send the message early).
- LinkedIn enables the Send button a moment after its editor has registered the text. The bot waits for that; if it never happens, it erases the box and retries by typing key by key.
- After clicking Send it waits for the box to empty, then reads the thread back and checks that the reply is the last message and is recognised as its own.

### Surviving LinkedIn's markup changes

Many of LinkedIn's class names are now hashed and change between builds. The bot uses what stays stable (roles, `aria-label`s, `data-` attributes, link targets, document structure) and keeps class names only as a second choice. The invitations page, which uses the newer hashed markup, is handled entirely by button role and label.

The bot is built to **fail safe**: when it cannot find or confirm something it does nothing, logs a warning, and `python -m bot doctor` shows which anchor stopped matching.

### Not flooding anyone

- At most 5 replies per poll.
- At most 40 replies per hour in total (`MAX_REPLIES_PER_HOUR`).
- At most 12 replies per conversation per hour (`MAX_REPLIES_PER_THREAD_PER_HOUR`).

These bound the damage of any bug, and stop two bots from talking to each other forever.

### Keeping secrets out

- The LinkedIn password is typed by the human into Chrome. The session lives in Chrome's own profile folder.
- The API key is read from `.env`.
- `.env`, `.session/` and `data/` are git-ignored.
- `doctor` hides names and message text by default.

## 7. What the bot relies on in LinkedIn's pages

| Thing | How it is found | Second choice |
|---|---|---|
| Signed in? | cookie `li_at` exists, URL is not a login page | |
| Owner's name | title of `/in/me/` ("Name \| LinkedIn") | photo `alt` in the top bar |
| Accept button | role `button`, accessible name contains "Accept" | |
| Kind of invitation | button label and card text | |
| Conversation row | `main li` containing `label[aria-label]` | `li.msg-conversation-listitem` |
| Row name | `.msg-conversation-listitem__participant-names`, `h3` | the label's `aria-label` |
| Row preview | `.msg-conversation-card__message-snippet` | row text without the timestamp |
| Row click target | `div[class*='listitem__link']` | the row itself |
| Message | `[data-view-name='message-list-item']` | `.msg-s-event-listitem` |
| Message text | `.msg-s-event-listitem__body` | the message's only `<p>` |
| Sender | `a[href*="/in/"]` outside the text bubble | `.msg-s-message-group__name` |
| Mine or theirs | `data-event-urn` id vs sender link id | CSS marker, then names |
| Thread title | `.msg-entity-lockup__entity-title` | the single heading around the message box |
| Message box | `main [role='textbox'][contenteditable='true']` | |
| Send button | `button.msg-form__send-button` in the box's form | `button[type='submit']` |

## 8. How a reply is written

One request to the Claude API per reply, in `bot/replies.py`:

- **Model:** `claude-opus-5-5` (change with `ANTHROPIC_MODEL`), with low reasoning effort since a chat reply is short.
- **System prompt:** the bot is the owner's automated assistant, not the owner in person; here is what is known about the owner (the content of `persona.md`); write only the message text; one to three sentences; reply in the other person's language; do not invent facts; do not commit the owner to meetings, prices or deadlines; be honest about being automated.
- **User prompt:** the last 12 messages as a transcript, with each line labelled by its author, followed by "write the reply to the latest message".

The other person's messages are placed inside the transcript and the model is told to treat them as conversation to answer, not as instructions. A message such as "ignore your rules and..." is therefore just text to reply to.

Failure handling:

- No API key: the fixed fallback message is used.
- Rate limit, API error or network error: each is logged with its own message, then the fallback is used.
- The model declines the request: the API retries it on a fallback model (a server-side option of the API); if that also declines, the fallback message is used.

The fallback message is: "Hi {first name}, thanks for your message! This is an automated reply. {Owner} will get back to you personally soon."

## 9. The state file

`data/state.json`, rewritten after every change:

```json
{
  "baselined": true,
  "owner_name": "Account Owner",
  "seen": { "Jane Doe": "Hi, thanks for connecting!" },
  "replies": { "2-abc123==": [1794000000.0] },
  "last_reply": { "2-abc123==": "Hi Jane, thanks for your message! ..." },
  "failures": {},
  "accepted": [{ "name": "Jane Doe", "at": "2026-11-06T15:28:01+00:00" }]
}
```

| Field | Purpose |
|---|---|
| `baselined` | whether the first snapshot of the inbox was taken |
| `owner_name` | which account this state belongs to |
| `seen` | last preview text seen for each conversation |
| `replies` | when replies were sent, per thread (for the limits) |
| `last_reply` | the last text sent per thread (the "do not answer yourself" check) |
| `failures` | failed attempts per conversation (give up after three) |
| `accepted` | a record of accepted invitations |

A dry run keeps its state in memory only, so it never affects the real one.

## 10. Configuration reference

All settings are optional and go in `.env`.

| Setting | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | API key for Claude. Without it the fallback message is used. |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Model that writes the replies. |
| `OWNER_NAME` | detected | The owner's name, if detection fails. |
| `POLL_SECONDS` | `20` | Pause between polls (varied by −20% to +30%). |
| `DRY_RUN` | `false` | Log what would be done; accept and send nothing. |
| `HEADLESS` | `false` | Run Chrome without a window (after signing in once). |
| `DISCLOSE_AUTOMATION` | `true` | Ask the model to say in its first reply that it is automated. |
| `MAX_REPLIES_PER_HOUR` | `40` | Total replies per hour. |
| `MAX_REPLIES_PER_THREAD_PER_HOUR` | `12` | Replies to one conversation per hour. |
| `ACCEPT_WORDS` | `Accept,Accepter` | Wording of the Accept button in the interface language. |
| `SELF_PREFIXES` | `You:,Vous :,Vous:` | How LinkedIn prefixes your own message in the list. |
| `BROWSER_CHANNEL` | `chrome` | Which browser Playwright drives. |

## 11. Tests

```powershell
python -m pytest
```

47 tests, about a minute and a half.

**Unit tests (`tests/test_units.py`, 21 tests)** cover the decision logic without a browser: which conversations are picked, when the baseline is taken, how message authors are resolved, the state file, the limits, the prompts, the account reset.

**End-to-end tests (`tests/test_flow.py`, 26 tests)** run the real bot code in a headless Chrome against `tests/fake_linkedin.py`. The fake is served by intercepting the browser's requests to `linkedin.com`, so the tests need no network and no account, and the bot is unaware of the difference.

The same conversation scenario runs against six variants of the fake's markup, each removing one kind of anchor:

| Variant | What is removed |
|---|---|
| classic | nothing |
| hashed class names | every meaningful class name |
| no incoming marker | the CSS class on the other person's messages |
| no thread title | the name above the thread |
| no data attributes | `data-event-urn` and sender links |
| hashed and no title | both |

Other cases covered: only connection requests are accepted; the first poll answers nothing; a message is answered exactly once; the owner's own last message is not answered; conversations without a message box are skipped; multi-line replies are sent as one message; per-conversation and hourly limits; dry run; an empty inbox receiving its first message; restart with saved state; the fallback reply without an API key; and two worst cases where the bot is made to misjudge who wrote a message and must still not answer itself.

## 12. What was checked on the real site

On 6 November 2026, on a real LinkedIn account:

- sign-in, and detection of the account owner's name;
- the conversation list and a thread are read correctly, and all three author clues agree;
- text typed in the message box makes Send clickable;
- one connection request was accepted by the bot;
- four messages were answered; each reply was read back and confirmed as the bot's own;
- the log of that session contains no warning and no error.

## 13. Limitations and known gaps

- **`persona.md` is sample data.** It was written to test the replies, and several facts in it are invented. It must be replaced with real information before real use.
- **Reply limits can silence the bot.** After 12 replies to one conversation in an hour it stops answering that conversation until the hour has passed. For a session with many quick messages, raise `MAX_REPLIES_PER_THREAD_PER_HOUR` and `MAX_REPLIES_PER_HOUR` in `.env`.
- **The automation notice depends on the model.** In one test the first reply said the owner was "away" without using the word "automated"; the second reply did say it.
- **A message that arrives while a reply is being written** is not taken into account in that reply.
- **Group conversations** are answered like one-to-one conversations.
- **Languages.** Button wording is matched for English and French; other interface languages need `ACCEPT_WORDS` and `SELF_PREFIXES`.
- **It needs a running computer** with Chrome open. It reloads two pages per poll.
- **Invitation acceptance was checked with one live invitation.** That page uses LinkedIn's newer markup, which changes more often.
- **LinkedIn's User Agreement does not allow automating the site.** This was built for a technical exercise on a personal account. Use it for short periods and at your own risk.

## 14. Acknowledgement

The structural anchors in `bot/dom.py` (conversation rows, message items, sender links) follow what the open-source [linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server) project documents about LinkedIn's current markup. No code was copied from it.
