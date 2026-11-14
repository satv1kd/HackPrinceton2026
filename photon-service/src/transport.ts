/**
 * Message transports for the Photon relay (Task 12).
 *
 * IMPORTANT — the PRD is wrong about Photon, and this file is where that
 * shows. The PRD says "a small TypeScript service runs on a Mac signed into
 * Messages and uses Photon's local iMessage SDK". Photon 2.0 is not that: it
 * is a **hosted** platform (https://api.photon.codes/v1) that owns the
 * phone lines. Nothing runs against the Messages app, and no Mac is needed.
 *
 * Two consequences that change the product, not just this file:
 *
 *  1. A shared Photon line **cannot send an unsolicited first message**. The
 *     recipient must first message the assigned number (the docs say to send
 *     exactly `ping` as an iMessage). Until they do, there is no conversation
 *     to reply into and sends fail. "Send to Mom" therefore only works for
 *     contacts who have messaged the line first.
 *  2. Sending is reply-shaped: it needs `spaceId`, `senderId` and
 *     `parentMessageId` taken from an incoming message, so this service has
 *     to find the conversation for a handle before it can send to it.
 *
 * Both are surfaced to the user as ordinary send failures with a readable
 * reason, which is what `static/js/send-buttons.js` renders.
 *
 * Transports are deliberately injectable so the relay can be exercised with
 * no credentials and no network (see tests/photon-service.test.mjs).
 */

export interface SendResult {
  ok: boolean;
  error?: string;
  /** Present on success when the platform returns an id. */
  messageId?: string;
}

export interface MessageTransport {
  readonly mode: string;
  send(to: string, text: string): Promise<SendResult>;
  health(): Promise<{ ok: boolean; detail: string }>;
}

/* ------------------------------------------------------------------ stub */

/**
 * Logs instead of sending.
 *
 * This is the default when no API key is configured, and it is what makes
 * the rest of the system demoable with no Photon account: the UI exercises
 * the full confirm -> send -> sent path and the text lands in the log.
 */
export function createStubTransport(
  log: (message: string) => void = console.log,
): MessageTransport {
  return {
    mode: "stub",
    async send(to, text) {
      log(`[photon:stub] would send to ${to}: ${text}`);
      return { ok: true, messageId: `stub-${Date.now()}` };
    },
    async health() {
      return { ok: true, detail: "stub transport; messages are logged, not sent" };
    },
  };
}

/* ---------------------------------------------------------------- photon */

export interface PhotonConfig {
  apiKey: string;
  projectId: string;
  /** Injectable for tests; defaults to the real SDK via dynamic import. */
  clientFactory?: () => Promise<PhotonLikeClient>;
}

/**
 * The slice of the Photon 2.0 SDK this service uses.
 *
 * Only these two calls are documented in the beta iMessage guide, and the
 * return shape of `sendMessage` is not specified there, so this interface
 * stays as small as possible. Everything uncertain about Photon is contained
 * in this file; the relay's own HTTP contract never changes.
 */
export interface PhotonLikeClient {
  projects: {
    listMessages(args: {
      path: { id: string };
      query?: Record<string, unknown>;
    }): Promise<{ data?: PhotonMessage[]; messages?: PhotonMessage[] }>;
    sendMessage(args: {
      path: { id: string };
      headers?: Record<string, string>;
      body: {
        spaceId: string;
        senderId: string;
        parentMessageId?: string;
        content: { type: "text"; format: "plain"; text: string };
      };
    }): Promise<{ id?: string } | undefined>;
  };
}

export interface PhotonMessage {
  id?: string;
  spaceId?: string;
  senderId?: string;
  /** The participant handle, e.g. "+14155550123". Field name varies. */
  from?: string;
  author?: { id?: string; handle?: string };
  handle?: string;
  createdAt?: string;
}

/** Strip formatting from a handle: digits only for phones, lowercase email. */
export function normalizeHandle(handle: string): string {
  const trimmed = (handle ?? "").trim();
  if (trimmed.includes("@")) return trimmed.toLowerCase();
  return trimmed.replace(/[\s().\-+]/g, "");
}

/**
 * The key two handles are compared on.
 *
 * Normalizing alone is not enough: contacts.json may hold `4155550123` while
 * Photon reports `+1 (415) 555-0123`, and those must be the same person or
 * the send fails for no visible reason. For numbers longer than ten digits
 * the last ten decide it, which is correct for NANP and a deliberate
 * approximation elsewhere — an international number that collides in its
 * last ten digits would be confused, which is far less likely than the
 * country-code mismatch this fixes.
 */
export function handleKey(handle: string): string {
  const normalized = normalizeHandle(handle);
  if (normalized.includes("@")) return normalized;
  return normalized.length > 10 ? normalized.slice(-10) : normalized;
}

/** Pull every handle-ish field off a message, as comparison keys. */
function handlesOf(message: PhotonMessage): string[] {
  return [message.from, message.handle, message.author?.handle]
    .filter((value): value is string => typeof value === "string" && value.length > 0)
    .map(handleKey);
}

export function createPhotonTransport(config: PhotonConfig): MessageTransport {
  const { apiKey, projectId } = config;
  const clientFactory = config.clientFactory ?? defaultClientFactory(apiKey);
  let client: PhotonLikeClient | null = null;

  async function getClient(): Promise<PhotonLikeClient> {
    if (client === null) client = await clientFactory();
    return client;
  }

  /**
   * Find the conversation to reply into for `to`.
   *
   * A shared line cannot start a conversation, so the only way to send is to
   * reply inside one the contact opened. If none is found that is a real,
   * explainable failure, not a bug.
   */
  async function findConversation(to: string): Promise<PhotonMessage | null> {
    const photon = await getClient();
    const response = await photon.projects.listMessages({ path: { id: projectId } });
    const messages = response?.data ?? response?.messages ?? [];
    const wanted = handleKey(to);
    const matches = messages.filter((message) => handlesOf(message).includes(wanted));
    if (matches.length === 0) return null;
    // Newest wins: reply into the live conversation, not a stale one.
    matches.sort(
      (a, b) => Date.parse(b.createdAt ?? "") - Date.parse(a.createdAt ?? "") || 0,
    );
    return matches[0];
  }

  return {
    mode: "live",

    async send(to, text) {
      let conversation: PhotonMessage | null;
      try {
        conversation = await findConversation(to);
      } catch (error) {
        return { ok: false, error: `could not read Photon conversations: ${describe(error)}` };
      }

      if (conversation === null) {
        return {
          ok: false,
          error:
            `no iMessage conversation with ${to} yet. A shared Photon line cannot ` +
            `send first: ask them to text "ping" to your assigned Photon number, ` +
            `then try again.`,
        };
      }
      if (!conversation.spaceId || !conversation.senderId) {
        return {
          ok: false,
          error: "the Photon conversation is missing spaceId or senderId",
        };
      }

      try {
        const photon = await getClient();
        const result = await photon.projects.sendMessage({
          path: { id: projectId },
          // Retries must not duplicate a message the user already sent.
          headers: { "idempotency-key": idempotencyKey(to, text) },
          body: {
            spaceId: conversation.spaceId,
            senderId: conversation.senderId,
            parentMessageId: conversation.id,
            content: { type: "text", format: "plain", text },
          },
        });
        // The docs say delivery is accepted asynchronously and not confirmed,
        // so "ok" here means accepted for dispatch, not delivered.
        return { ok: true, messageId: result?.id };
      } catch (error) {
        return { ok: false, error: describe(error) };
      }
    },

    async health() {
      if (!apiKey || !projectId) {
        return { ok: false, detail: "PHOTON_API_KEY or PHOTON_PROJECT_ID is not set" };
      }
      try {
        const photon = await getClient();
        await photon.projects.listMessages({ path: { id: projectId } });
        return { ok: true, detail: "Photon API reachable" };
      } catch (error) {
        return { ok: false, detail: `Photon API unreachable: ${describe(error)}` };
      }
    },
  };
}

function defaultClientFactory(apiKey: string): () => Promise<PhotonLikeClient> {
  return async () => {
    let module: Record<string, unknown>;
    try {
      // Dynamic so the service starts, and runs in stub mode, with no deps
      // installed. The package name is from photon.codes/docs/beta.
      module = (await import("@photon-ai/api")) as Record<string, unknown>;
    } catch (error) {
      throw new Error(
        "@photon-ai/api is not installed. Run `npm install` in photon-service/, " +
          "or leave PHOTON_API_KEY unset to run in stub mode.",
      );
    }
    const Photon = (module.Photon ?? module.default) as
      | (new (options: unknown) => PhotonLikeClient)
      | undefined;
    if (typeof Photon !== "function") {
      throw new Error("@photon-ai/api did not export a Photon constructor");
    }
    return new Photon({ headers: { Authorization: `Bearer ${apiKey}` } });
  };
}

function idempotencyKey(to: string, text: string): string {
  // Same recipient + same text within the same second is a double-tap, not
  // two intended messages.
  const bucket = Math.floor(Date.now() / 1000);
  return `heard-${handleKey(to)}-${bucket}-${hash(text)}`;
}

function hash(value: string): string {
  let h = 0;
  for (let i = 0; i < value.length; i++) h = (Math.imul(31, h) + value.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

function describe(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}
