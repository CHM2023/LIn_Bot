# LinkedIn auto-responder bot

A bot that runs on your own LinkedIn account and, by itself:

- **accepts incoming connection requests**
- **answers new direct messages**, using the conversation so far and a short profile of you

> **Note.** This file and [README_DETAILED.md](README_DETAILED.md) were written on 6 November 2026, **before the timed test was started**. They describe the bot as it was at that point (branch `before-start`). Changes made during the test are on `after-start` and may not be reflected here.

## Try it

The bot runs on a dedicated test account: **[Jack Helou](https://www.linkedin.com/in/jack-helou-a1492a449/)**. Send it a connection request, then a message once it has accepted. It answers while the bot is running.

The account was created for this exercise so that automation would not put the author's personal profile at risk. Author: [Charbel Hajj Moussa](https://www.linkedin.com/in/charbelhajjmoussa7/).

## Setup (once)

You need Windows, Python 3.10 or newer, and Google Chrome.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

1. Open `.env` and set `ANTHROPIC_API_KEY`. Without it the bot still answers, but with one fixed message.
2. Describe yourself in `persona.md`. The bot answers questions from it.
3. Sign in: `python -m bot login`. Chrome opens; sign in with your LinkedIn e-mail and password. This is needed only once.

## Run

```powershell
python -m bot run
```

Leave the Chrome window open and the computer awake. Stop with `Ctrl+C`.

What to expect:

- The first poll only looks at the inbox. Messages that were already there are never answered.
- A connection request is accepted within about 30 seconds.
- A new message is answered within about a minute.
- Everything it does is printed, and saved in `data/bot.log`.

## Commands

| Command | What it does |
|---|---|
| `python -m bot login` | Sign in once. Add `--switch` to sign in with another account. |
| `python -m bot run` | Run the bot until `Ctrl+C`. |
| `python -m bot run --dry-run` | Show what it would accept and reply; do nothing. |
| `python -m bot doctor` | Check that the bot can still read LinkedIn's pages. |
| `python -m pytest` | Run the tests (no LinkedIn account needed). |

## Settings (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Lets Claude write the replies |
| `POLL_SECONDS` | `20` | Pause between two checks |
| `MAX_REPLIES_PER_THREAD_PER_HOUR` | `12` | Replies to one conversation per hour |
| `MAX_REPLIES_PER_HOUR` | `40` | Replies in total per hour |
| `DISCLOSE_AUTOMATION` | `true` | The first reply says it is automated |
| `DRY_RUN` | `false` | Same as `--dry-run` |

Every setting is listed in [README_DETAILED.md](README_DETAILED.md#10-configuration-reference).

## Good to know

- **`persona.md` in this repository is sample data.** It was written to test the replies and several facts in it are invented. Replace it with your own before real use.
- **Reply limits.** When a limit above is reached the bot stays silent until the hour has passed. If you expect many messages from one person in a short time, raise the two `MAX_REPLIES_…` values in `.env`.
- **Changing account.** Run `python -m bot login --switch`. The bot notices the new account and starts from a clean state.
- **Never commit `.env`, `.session/` or `data/`.** They hold your API key, your LinkedIn session and your messages. They are already git-ignored.
- **LinkedIn's User Agreement does not allow automating the site.** This was built for a technical exercise. Use it for short periods and at your own risk.

How it works, the libraries used and a description of every file: [README_DETAILED.md](README_DETAILED.md).
