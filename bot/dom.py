"""Everything the bot knows about LinkedIn's markup lives in this file.

LinkedIn has no public API for invitations or messaging, so the bot reads the
same pages a person would. LinkedIn's CSS class names change (many are now
hashed per build), so the primary anchors here are the things that stay:
roles, aria-labels, `data-` attributes, link targets and document structure.
Class names are kept only as a second choice.

A markup change on LinkedIn's side is the most likely thing to break the bot.
When it happens this is the only file to edit, and `python -m bot doctor`
reports which anchors still match.
"""

from __future__ import annotations

FEED_PATH = "/feed/"
LOGIN_PATH = "/login"
OWN_PROFILE_PATH = "/in/me/"  # redirects to the signed-in member's profile
INVITATIONS_PATH = "/mynetwork/invitation-manager/received/"
# The compose page shows the conversation list without opening (and thereby
# marking as read) the most recent conversation, which plain /messaging/ does.
MESSAGING_PATH = "/messaging/compose/"

# --- Messaging: the conversation list ---------------------------------------
# Each row is an <li> holding a <label aria-label="Select conversation with X">.
CONVERSATION_ROWS = "main li:has(label[aria-label])"
CONVERSATION_ROWS_BY_CLASS = "li.msg-conversation-listitem"

CONVERSATION = {
    "rowsByClass": CONVERSATION_ROWS_BY_CLASS,
    "labelPrefixes": ["Select conversation with", "Sélectionner la conversation avec"],
    "name": [
        ".msg-conversation-listitem__participant-names",
        ".msg-conversation-card__participant-names",
        "h3",
    ],
    "snippet": [
        ".msg-conversation-card__message-snippet",
        ".msg-conversation-card__message-snippet-body",
    ],
}
# The element inside a row that reacts to a click.
CONVERSATION_LINK = ["div[class*='listitem__link']", ".msg-conversation-card"]

# --- Messaging: the open thread ----------------------------------------------
THREAD = {
    "items": [
        "[data-view-name='message-list-item']",
        ".msg-s-event-listitem",
        "li.msg-s-message-list__event",
    ],
    "body": [".msg-s-event-listitem__body", ".msg-s-event__content"],
    "sender": [".msg-s-message-group__name"],
    "title": [".msg-entity-lockup__entity-title", ".msg-thread__link-to-profile"],
    # Class LinkedIn puts on messages written by the *other* person.
    "marker": "msg-s-event-listitem--other",
}

COMPOSE_BOX = "main [role='textbox'][contenteditable='true']"
SEND_BUTTON = ["button.msg-form__send-button", "button[type='submit']"]

# --- Global navigation: used to learn the account owner's name --------------
OWNER_PHOTO = ["img.global-nav__me-photo", ".global-nav__me img"]


# The reading is done by small functions that run inside the page. They only
# read; every decision is taken in Python.
LIST_CONVERSATIONS_JS = """
(sel) => {
  const first = (root, list) => {
    for (const s of list) { const el = root.querySelector(s); if (el) return el; }
    return null;
  };
  const squash = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const main = document.querySelector('main');
  let rows = main
    ? Array.from(main.querySelectorAll('li')).filter((li) => li.querySelector('label[aria-label]'))
    : [];
  let mode = 'label';
  if (!rows.length) {
    rows = Array.from(document.querySelectorAll(sel.rowsByClass));
    mode = 'class';
  }
  const cards = rows.map((li, index) => {
    const label = li.querySelector('label[aria-label]');
    let labelled = label ? squash(label.getAttribute('aria-label')) : '';
    for (const prefix of sel.labelPrefixes) {
      if (labelled.toLowerCase().startsWith(prefix.toLowerCase())) {
        labelled = labelled.slice(prefix.length).trim();
      }
    }
    const nameEl = first(li, sel.name);
    const name = squash(nameEl ? nameEl.innerText : '') || labelled;
    const snippetEl = first(li, sel.snippet);
    let snippet = squash(snippetEl ? snippetEl.innerText : '');
    if (!snippet) {
      // No recognisable preview element: use the row's text, minus the parts
      // that change on their own (the timestamp) or repeat the name.
      const copy = li.cloneNode(true);
      copy.querySelectorAll('time, label, h3').forEach((node) => node.remove());
      snippet = squash(copy.textContent);
    }
    return { index, mode, name, snippet };
  }).filter((card) => card.name);
  return { listFound: rows.length > 0, mode, cards };
}
"""

READ_THREAD_JS = """
(sel) => {
  const first = (root, list) => {
    for (const s of list) { const el = root.querySelector(s); if (el) return el; }
    return null;
  };
  const text = (el) =>
    el ? el.innerText.replace(/[ \\t\\u00a0]+/g, ' ').replace(/\\n{2,}/g, '\\n').trim() : '';
  const visible = (el) =>
    !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const profileId = (anchor) => {
    try {
      const path = new URL(anchor.getAttribute('href') || '', location.href).pathname;
      const match = /^\\/in\\/([^/]+)/.exec(path);
      return match ? decodeURIComponent(match[1]) : '';
    } catch { return ''; }
  };

  let items = [];
  for (const s of sel.items) {
    items = Array.from(document.querySelectorAll(s));
    if (items.length) break;
  }
  const messages = [];
  let senderName = '';
  let senderId = '';
  for (const item of items) {
    const body = first(item, sel.body) ||
      (item.querySelectorAll('p').length === 1 ? item.querySelector('p') : null);
    if (!body) continue; // date separators and system notices
    const scope = item.closest('li') || item;

    // LinkedIn heads the first message of a group with links to its sender's
    // profile; follow-ups from the same sender have no header. Links inside
    // the message text itself must not count.
    let bubble = body;
    for (let hops = 0; hops < 2; hops++) {
      const up = bubble.parentElement;
      if (up && up !== item && item.contains(up)) bubble = up;
    }
    const links = Array.from(scope.querySelectorAll('a[href*="/in/"]'))
      .filter((anchor) => !bubble.contains(anchor));
    const nameEl = first(scope, sel.sender);
    if (links.length) {
      senderId = profileId(links[0]);
      senderName = text(nameEl) || links.map(text).find(Boolean) || '';
    } else if (nameEl) {
      senderName = text(nameEl);
    }

    // Every message carries an id that starts with the signed-in member's
    // profile id. A message is ours when its sender link has that same id.
    const holder = item.hasAttribute('data-event-urn')
      ? item
      : item.querySelector('[data-event-urn]') || item.closest('[data-event-urn]');
    const urn = holder ? (holder.getAttribute('data-event-urn') || '').trim() : '';
    const member =
      (/^urn:li:msg_message:\\(urn:li:fsd_profile:([A-Za-z0-9_-]+),/.exec(urn) || [])[1] || '';

    messages.push({
      sender: senderName,
      text: text(body),
      mine: member && senderId ? senderId === member : null,
      marked: item.classList.contains(sel.marker) || !!scope.querySelector('.' + sel.marker),
    });
  }

  // The name above the thread: by class (never one inside a list row or a
  // message), else the single heading that sits around the message box.
  let title = '';
  for (const s of sel.title) {
    const found = Array.from(document.querySelectorAll(s))
      .find((node) => visible(node) && !node.closest('li'));
    if (found) { title = text(found); break; }
  }
  if (!title) {
    const editors = Array.from(
      document.querySelectorAll('main [role="textbox"][contenteditable="true"]')
    ).filter(visible);
    if (editors.length === 1) {
      const main = editors[0].closest('main');
      let pane = editors[0].parentElement;
      while (pane && pane !== main.parentElement) {
        const headings = Array.from(pane.querySelectorAll('h1, h2')).filter(
          (h) => visible(h) && !h.closest('li, [data-view-name="message-list-item"]')
        );
        if (headings.length === 1) title = text(headings[0]);
        if (headings.length >= 1) break;
        pane = pane.parentElement;
      }
    }
  }
  return { title, messages };
}
"""

# The text of the invitation card a button belongs to: the nearest list item,
# else the largest ancestor that holds no other matching button.
INVITATION_CARD_TEXT_JS = """
(button, pattern) => {
  const accept = new RegExp(pattern, 'i');
  const card = button.closest('li, [role="listitem"], article');
  if (card) return card.innerText || '';
  let node = button;
  while (node.parentElement && node.parentElement !== document.body) {
    const others = Array.from(node.parentElement.querySelectorAll('button, [role="button"]'))
      .filter((b) => accept.test(b.getAttribute('aria-label') || b.innerText || ''));
    if (others.length > 1) break;
    node = node.parentElement;
  }
  return node.innerText || '';
}
"""
