"""A tiny stand-in for LinkedIn.

It is served to the browser through request interception, so the bot runs its
whole flow (open pages, click Accept, read threads, type, send) without any
network access and without touching a real account.

The markup mimics what the bot relies on in `bot/dom.py`, and the constructor
flags remove one kind of anchor at a time (class names, data attributes, the
thread title...) to prove the bot still works, or fails safely, without it.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

OWNER = "Charbel Test"
OWNER_ID = "ACoAAOwner123"

SHELL = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body>
<header id="global-nav">{photo}</header>
{body}
</body></html>"""

INVITATIONS_BODY = """
<main>
  <ul id="invitations"></ul>
</main>
<script>
const CARDS = {cards};
const list = document.getElementById('invitations');
for (const card of CARDS) {{
  const li = document.createElement('li');
  const about = document.createElement('p');
  about.textContent = card.text;
  const accept = document.createElement('button');
  accept.setAttribute('aria-label', card.label);
  accept.textContent = 'Accept';
  accept.addEventListener('click', async () => {{
    await fetch('/fake-api/accept?label=' + encodeURIComponent(card.label));
    li.remove();
  }});
  const ignore = document.createElement('button');
  ignore.setAttribute('aria-label', card.label.replace('Accept', 'Ignore'));
  ignore.textContent = 'Ignore';
  li.append(about, accept, ignore);
  list.appendChild(li);
}}
</script>
"""

MESSAGING_BODY = """
<main>
  <ul id="conversations"></ul>
  <section id="pane">
    <div id="header"></div>
    <ul id="events"></ul>
    <form id="composer">
      <div id="editor" contenteditable="true" role="textbox"
           style="min-height: 40px; border: 1px solid #999"></div>
      <button id="send" type="submit" disabled>Send</button>
    </form>
  </section>
</main>
<script>
const DATA = {data};
let current = null;
// With DATA.hashed, class names are meaningless strings, as on the real site.
const cls = (name) => (DATA.hashed ? 'x' + btoa(name).slice(0, 8) : name);
const el = (tag, className, text) => {{
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}};
const list = document.getElementById('conversations');
const events = document.getElementById('events');
const form = document.getElementById('composer');
const box = document.getElementById('editor');
const send = document.getElementById('send');
list.className = cls('msg-conversations-container__conversations-list');
events.className = cls('msg-s-message-list-content');
form.className = cls('msg-form');
box.className = cls('msg-form__contenteditable');
send.className = cls('msg-form__send-button');

function snippet(thread) {{
  const last = thread.messages[thread.messages.length - 1];
  return (last.mine ? 'You: ' : '') + last.text;
}}

function renderList() {{
  list.replaceChildren();
  for (const thread of DATA.threads) {{
    const li = el('li', cls('msg-conversation-listitem'));
    const label = el('label');
    label.setAttribute('aria-label', 'Select conversation with ' + thread.name);
    label.appendChild(el('input')).type = 'checkbox';
    // The one class the real site keeps recognisable: the click target.
    const link = el('div', 'msg-conversation-listitem__link');
    link.append(
      el(DATA.hashed ? 'span' : 'h3', cls('msg-conversation-listitem__participant-names'), thread.name),
      el('p', cls('msg-conversation-card__message-snippet'), snippet(thread)),
      el('time', '', new Date().toISOString()),  // changes on every load
    );
    link.addEventListener('click', () => openThread(thread.id));
    li.append(label, link);
    list.appendChild(li);
  }}
}}

function renderThread() {{
  const thread = DATA.threads.find((t) => t.id === current);
  const header = document.getElementById('header');
  header.replaceChildren();
  if (DATA.title) {{
    header.appendChild(
      el('h2', cls('msg-entity-lockup__entity-title'), thread ? thread.name : 'New message')
    );
  }}
  events.replaceChildren();
  if (!thread) return;
  let previous = null;
  thread.messages.forEach((message, number) => {{
    const li = el('li', cls('msg-s-message-list__event'));
    const item = el('div', cls('msg-s-event-listitem'));
    if (!message.mine && DATA.markers && !DATA.hashed) item.classList.add('msg-s-event-listitem--other');
    if (DATA.ids) {{
      item.setAttribute('data-view-name', 'message-list-item');
      item.setAttribute(
        'data-event-urn',
        'urn:li:msg_message:(urn:li:fsd_profile:' + DATA.ownerId + ',2-' + thread.id + '-' + number + ')'
      );
    }}
    if (previous !== message.mine) {{
      // Like LinkedIn: the sender is named once per group of messages.
      const name = message.mine ? DATA.owner : thread.name;
      const id = message.mine ? DATA.ownerId : thread.profileId;
      const slug = name.toLowerCase().replace(/ /g, '-');
      const meta = el('div', cls('msg-s-message-group__meta'));
      const nameEl = el('span', cls('msg-s-message-group__name'), name);
      if (DATA.ids) {{
        const href = '/in/' + (DATA.vanity ? slug : id) + '/';
        const avatar = el('a');
        avatar.href = href;
        avatar.appendChild(el('img')).alt = '';
        const profile = el('a');
        profile.href = href;
        profile.appendChild(nameEl);
        meta.appendChild(profile);
        item.appendChild(avatar);
      }} else {{
        meta.appendChild(nameEl);
      }}
      meta.appendChild(el('time', '', '10:42 AM'));
      item.appendChild(meta);
    }}
    const bubble = el('div', cls('msg-s-event-listitem__message-bubble'));
    const content = el('div', cls('msg-s-event__content'));
    const body = el('p', cls('msg-s-event-listitem__body'), message.text);
    body.style.whiteSpace = 'pre-wrap';
    content.appendChild(body);
    bubble.appendChild(content);
    item.appendChild(bubble);
    li.appendChild(item);
    events.appendChild(li);
    previous = message.mine;
  }});
  form.style.display = thread.locked ? 'none' : '';
}}

function openThread(id) {{
  current = id;
  history.pushState({{}}, '', '/messaging/thread/' + id + '/');
  // The real site fetches the thread: the old one stays on screen meanwhile.
  setTimeout(renderThread, 150);
}}

box.addEventListener('input', () => {{ send.disabled = box.innerText.trim() === ''; }});
form.addEventListener('submit', async (event) => {{
  event.preventDefault();
  const text = box.innerText.trim();
  if (!text || !current) return;
  await fetch('/fake-api/send', {{ method: 'POST', body: JSON.stringify({{ id: current, text }}) }});
  const thread = DATA.threads.find((t) => t.id === current);
  thread.messages.push({{ mine: true, text }});
  DATA.threads = [thread, ...DATA.threads.filter((t) => t !== thread)];
  box.replaceChildren();
  send.disabled = true;
  renderList();
  renderThread();
}});

renderList();
renderThread();
// Plain /messaging/ opens the most recent conversation by itself.
if (DATA.autoOpen && DATA.threads.length) openThread(DATA.threads[0].id);
</script>
"""


def _profile_id(name: str) -> str:
    return "ACoAA" + "".join(ch for ch in name if ch.isalnum())


class FakeLinkedIn:
    def __init__(
        self,
        hashed: bool = False,  # class names are meaningless (as on the real site today)
        markers: bool = True,  # the "--other" class on incoming messages
        title: bool = True,  # the contact's name above the open thread
        ids: bool = True,  # data-event-urn + sender profile links
        vanity: bool = False,  # sender links use /in/<name>/ instead of the profile id
        owner_photo: bool = True,  # the owner's name in the top bar
    ):
        self.flags = {
            "hashed": hashed,
            "markers": markers,
            "title": title,
            "ids": ids,
            "vanity": vanity,
        }
        self.owner_photo = owner_photo
        self.logged_in = True
        self.invitations = [
            {"label": "Accept Jane Doe’s invitation", "text": "Jane Doe · Recruiter at Acme"},
            {
                "label": "Accept invitation to subscribe to Tech Weekly",
                "text": "Sam Lee invited you to subscribe to Tech Weekly",
            },
            {
                # A page invitation whose button label looks like a person's.
                "label": "Accept Acme Corp’s invitation",
                "text": "Dana Roy invited you to follow Acme Corp",
            },
            {"label": "Accept Omar Haddad’s invitation", "text": "Omar Haddad · Event Manager"},
        ]
        # Most recent first, like the real inbox.
        self.threads = [
            self._thread("Old Contact", [(False, "Are you still interested in the role?")]),
            self._thread("Sara Khoury", [(False, "Thanks for the help!"), (True, "You're welcome.")]),
        ]
        self.accepted: list[str] = []
        self.sent: list[tuple[str, str]] = []

    @staticmethod
    def _thread(name: str, messages: list[tuple[bool, str]], locked: bool = False) -> dict:
        return {
            "id": "t-" + name.lower().replace(" ", "-"),
            "name": name,
            "profileId": _profile_id(name),
            "locked": locked,
            "messages": [{"mine": mine, "text": text} for mine, text in messages],
        }

    # --- things the test can make happen ------------------------------------
    def receive(self, name: str, text: str, mine: bool = False, locked: bool = False) -> None:
        """A message arrives (or, with mine=True, the owner writes from their phone)."""
        thread = next((t for t in self.threads if t["name"] == name), None)
        if thread is None:
            thread = self._thread(name, [], locked)
        thread["messages"].append({"mine": mine, "text": text})
        self.threads = [thread] + [t for t in self.threads if t is not thread]

    # --- plumbing -----------------------------------------------------------
    def install(self, context) -> None:
        if self.logged_in:
            context.add_cookies(
                [{"name": "li_at", "value": "fake-session", "url": "https://www.linkedin.com"}]
            )
        context.route("**/*", self._handle)

    def _html(self, body: str, title: str = "LinkedIn") -> str:
        photo = f'<img class="global-nav__me-photo" alt="{OWNER}">' if self.owner_photo else ""
        return SHELL.format(photo=photo, body=body, title=title)

    def _handle(self, route) -> None:
        url = urlparse(route.request.url)
        if url.hostname != "www.linkedin.com":
            route.abort()
            return
        if url.path == "/fake-api/accept":
            label = parse_qs(url.query)["label"][0]
            self.invitations = [card for card in self.invitations if card["label"] != label]
            self.accepted.append(label)
            route.fulfill(status=200, body="ok")
            return
        if url.path == "/fake-api/send":
            data = json.loads(route.request.post_data)
            thread = next(t for t in self.threads if t["id"] == data["id"])
            thread["messages"].append({"mine": True, "text": data["text"]})
            self.threads = [thread] + [t for t in self.threads if t is not thread]
            self.sent.append((thread["name"], data["text"]))
            route.fulfill(status=200, body="ok")
            return
        if url.path.startswith("/mynetwork/invitation-manager"):
            body = INVITATIONS_BODY.format(cards=json.dumps(self.invitations))
        elif url.path.startswith("/messaging"):
            data = {
                **self.flags,
                "owner": OWNER,
                "ownerId": OWNER_ID,
                "autoOpen": url.path == "/messaging/",
                "threads": self.threads,
            }
            body = MESSAGING_BODY.format(data=json.dumps(data).replace("</", "<\\/"))
        elif url.path.startswith("/in/"):
            # The owner's own profile: the page title carries their name.
            page = self._html("<main><p>Profile</p></main>", title=f"(2) {OWNER} | LinkedIn")
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=page)
            return
        else:
            body = "<main><p>Feed</p></main>"
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self._html(body))
