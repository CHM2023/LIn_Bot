"""The bot's memory between polls (and between restarts): one small JSON file.

It answers three questions:
  * Have I already looked at this conversation in its current form?  -> `seen`
  * How many replies did I send recently?                            -> `replies`
  * Which invitations did I accept?                                  -> `accepted`
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HOUR = 3600.0


@dataclass
class State:
    path: Path | None = None  # None = keep in memory only (dry runs, tests)
    baselined: bool = False
    seen: dict[str, str] = field(default_factory=dict)  # conversation name -> last snippet
    replies: dict[str, list[float]] = field(default_factory=dict)  # thread -> send times
    last_reply: dict[str, str] = field(default_factory=dict)  # thread -> text we sent last
    failures: dict[str, int] = field(default_factory=dict)  # conversation name -> failed tries
    accepted: list[dict] = field(default_factory=list)
    owner_name: str = ""  # the account owner's name, looked up once
    baseline_checks: int = 0  # not saved: counts looks at the inbox before the baseline

    @classmethod
    def load(cls, path: Path | None) -> "State":
        if path is None or not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            baselined=bool(raw.get("baselined", False)),
            seen=dict(raw.get("seen", {})),
            replies={k: list(v) for k, v in raw.get("replies", {}).items()},
            last_reply=dict(raw.get("last_reply", {})),
            failures=dict(raw.get("failures", {})),
            accepted=list(raw.get("accepted", [])),
            owner_name=str(raw.get("owner_name", "")),
        )

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "baselined": self.baselined,
            "seen": self.seen,
            "replies": self.replies,
            "last_reply": self.last_reply,
            "failures": self.failures,
            "accepted": self.accepted,
            "owner_name": self.owner_name,
        }
        # Write to a temp file first so a crash mid-write cannot corrupt the state.
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- account ------------------------------------------------------------
    def for_account(self, account: str) -> "State":
        """Return the state to use for the account that is signed in now.

        The saved state describes one inbox. With a different account signed
        in, every conversation in the new inbox would look new and old
        messages would get answered, so the bot starts from a clean state
        instead. An empty `account` means the name could not be read: keep
        what we have.
        """

        def key(name: str) -> str:
            return " ".join(name.split()).casefold()

        if account and self.owner_name and key(account) != key(self.owner_name):
            return State(path=self.path, owner_name=account)
        if account:
            self.owner_name = account
        return self

    # --- conversations ------------------------------------------------------
    def is_new(self, name: str, snippet: str) -> bool:
        return self.seen.get(name) != snippet

    def remember(self, name: str, snippet: str) -> None:
        self.seen[name] = snippet
        self.failures.pop(name, None)

    def note_failure(self, name: str) -> int:
        self.failures[name] = self.failures.get(name, 0) + 1
        return self.failures[name]

    # --- replies ------------------------------------------------------------
    def replies_last_hour(self, thread: str | None = None, now: float | None = None) -> int:
        cutoff = (now if now is not None else time.time()) - HOUR
        threads = [thread] if thread is not None else list(self.replies)
        return sum(1 for key in threads for sent in self.replies.get(key, []) if sent >= cutoff)

    def has_replied(self, thread: str) -> bool:
        return bool(self.replies.get(thread))

    def record_reply(self, thread: str, text: str = "", now: float | None = None) -> None:
        now = now if now is not None else time.time()
        recent = [sent for sent in self.replies.get(thread, []) if sent >= now - HOUR]
        self.replies[thread] = recent + [now]
        if text:
            self.last_reply[thread] = text

    # --- invitations --------------------------------------------------------
    def record_accept(self, name: str) -> None:
        self.accepted.append({"name": name, "at": datetime.now(timezone.utc).isoformat()})
