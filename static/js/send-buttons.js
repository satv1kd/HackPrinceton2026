/**
 * "Send to <contact>" buttons for the confirmed result card (Task 13).
 *
 * The Talk-mode result card (Task 10) owns the card itself; this module owns
 * the row of send buttons inside it. Wiring, once a result has been confirmed
 * by a Yes tap or a picker tap:
 *
 *     import { renderSendButtons } from "./send.js";
 *     renderSendButtons({ container: cardFooter, text: confirmedText, confirmed: true });
 *
 * Deliberate constraint: `confirmed` must be `true` or nothing renders. The
 * PRD requires that send buttons appear only after confirmation so wrong text
 * is never sent, and the server cannot enforce that (the feedback store in
 * Task 6 owns confirmation state), so the gate lives here and fails closed.
 */

const CONTACTS_ENDPOINT = "/api/contacts";
const SEND_ENDPOINT = "/api/send";

/** Contacts change rarely and are hand-edited; fetch once per page load. */
let contactsPromise = null;

export function loadContacts({ force = false } = {}) {
  if (force || !contactsPromise) {
    contactsPromise = fetch(CONTACTS_ENDPOINT)
      .then((response) => {
        if (!response.ok) throw new Error(`contacts unavailable (HTTP ${response.status})`);
        return response.json();
      })
      .catch((error) => {
        contactsPromise = null; // let a later render retry
        throw error;
      });
  }
  return contactsPromise;
}

/**
 * Render a send button per contact into `container`.
 *
 * @param {object}      options
 * @param {HTMLElement} options.container  cleared and filled
 * @param {string}      options.text       the confirmed text to send
 * @param {boolean}     options.confirmed  must be true
 * @param {Function}   [options.onStatus]  (state, detail) => void, for the
 *                                         card's own status line. state is
 *                                         "sending" | "sent" | "failed".
 * @returns {Promise<void>}
 */
export async function renderSendButtons({ container, text, confirmed, onStatus }) {
  if (!container) throw new Error("renderSendButtons needs a container");
  container.innerHTML = "";

  if (confirmed !== true) {
    // Not an error: the card calls this on every result and only passes
    // confirmed once the user has tapped.
    return;
  }

  const message = (text || "").trim();
  if (!message) return;

  let contacts;
  try {
    contacts = await loadContacts();
  } catch (error) {
    container.appendChild(buildNotice(`Can't load contacts. ${error.message}`));
    return;
  }

  if (!contacts.length) {
    // No contacts configured yet; say so rather than showing an empty row.
    container.appendChild(buildNotice("No contacts set up yet."));
    return;
  }

  const row = document.createElement("div");
  row.className = "send-row";
  for (const contact of contacts) {
    row.appendChild(buildSendButton(contact, message, onStatus));
  }
  container.appendChild(row);
}

function buildSendButton(contact, message, onStatus) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "send-button";
  button.dataset.contactId = contact.id;
  button.dataset.state = "idle";
  button.setAttribute("aria-label", `Send to ${contact.name}`);

  const label = `${contact.emoji ? contact.emoji + " " : ""}Send to ${contact.name}`;
  setButtonLabel(button, label);

  button.addEventListener("click", async () => {
    if (button.dataset.state === "sending" || button.dataset.state === "sent") return;

    button.dataset.state = "sending";
    button.disabled = true;
    setButtonLabel(button, "Sending…");
    onStatus?.("sending", contact);

    const { ok, error } = await postSend(message, contact.id);

    if (ok) {
      button.dataset.state = "sent";
      // Icon as well as colour: the PRD requires state never be carried by
      // colour alone.
      setButtonLabel(button, `✓ Sent to ${contact.name}`);
      onStatus?.("sent", contact);
      return;
    }

    button.dataset.state = "failed";
    button.disabled = false;
    setButtonLabel(button, `✗ Didn't send — tap to try again`);
    button.title = error || "";
    onStatus?.("failed", { contact, error });
  });

  return button;
}

async function postSend(text, contactId) {
  try {
    const response = await fetch(SEND_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, contact_id: contactId }),
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        if (body?.detail) detail = body.detail;
      } catch {
        /* keep the status-code detail */
      }
      return { ok: false, error: detail };
    }
    const body = await response.json();
    return { ok: body.ok === true, error: body.error || null };
  } catch (error) {
    return { ok: false, error: error.message || "network error" };
  }
}

function setButtonLabel(button, text) {
  button.textContent = text;
}

function buildNotice(text) {
  const notice = document.createElement("p");
  notice.className = "send-notice";
  notice.textContent = text;
  return notice;
}
