/**
 * Message transports for the Photon relay (Task 12).
 *
 * IMPORTANT — the PRD is wrong about Photon, and this file is where that
 * shows. The PRD says "a small TypeScript service runs on a Mac signed into
 * Messages and uses Photon's local iMessage SDK". Photon 2.0 is not that: it
 * is a **hosted** platform that owns the phone lines. Nothing runs against
 * the Messages app, and no Mac is needed.
 *
 * The consequence that changes the product, not just this file: a shared
 * Photon line **cannot send an unsolicited first message**. The recipient has
 * to message the assigned number first (send exactly `ping` as an iMessage).
 * Until they do, the API refuses the send with `SHARED_LINE_INBOUND_REQUIRED`.
 * "Send to Mom" therefore only works for contacts who have texted the line.
 *
 * That is surfaced to the user as an ordinary send failure with a readable
 * reason, which is what `static/js/send-buttons.js` renders.
 *
 * ## How the shapes here were established
 *
 * The call shapes below are checked against the TypeScript declarations
 * published in `@photon-ai/api@0.6.0` — the namespace (`projects`), the
 * `{ path, body, headers }` input envelope, the required `idempotency-key`
 * header, the `{ items: [...] }` list envelope, and the `SHARED_LINE_*`
 * problem codes all come from that package's `.d.ts` files rather than from
 * prose. **No call has been made against the live API**, because no account
 * was available; see photon-service/README.md for what that leaves open.
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

/** Every call in this service is iMessage; Photon also speaks sms/email. */
const PLATFORM = "imessage";

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
  /**
   * Our own line's Photon user id. Optional: when unset it is discovered by
   * listing the project's users and taking the one that is a line. Set
   * `PHOTON_SENDER_ID` to pin it if the project has more than one.
   */
  senderId?: string;
  /** Injectable for tests; defaults to the real SDK via dynamic import. */
  clientFactory?: () => Promise<PhotonLikeClient>;
}

/**
 * A Photon user: either a contact, or one of our own lines.
 *
 * `id` is optional because the SDK's `User` is a union that includes an
 * `{ platform: 'UNKNOWN', raw }` variant carrying no id — a user on a
 * platform this client version does not model. Every read of `id` here is
 * guarded accordingly.
 */
export interface PhotonUser {
  id?: string;
  handle?: string;
  platform?: string;
  /** `type` is 'shared' | 'dedicated' on a line, and absent on a contact. */
  details?: { type?: string; resourceId?: string };
  /**
   * The SDK's resource types are open (`[x: string]: unknown`) and carry many
   * fields this service ignores. Without this, TypeScript's weak-type check
   * rejects a real resource as having nothing in common with our narrow view.
   */
  [key: string]: unknown;
}

/** A conversation. Narrow view of the SDK's `Space` union; see PhotonUser. */
export interface PhotonSpace {
  id?: string;
  [key: string]: unknown;
}

/** A sent message. Narrow view of the SDK's `Message` union. */
export interface PhotonSentMessage {
  id?: string;
  status?: string;
  [key: string]: unknown;
}

/**
 * The slice of the Photon 2.0 SDK this service uses.
 *
 * Shapes follow `@photon-ai/api@0.6.0`'s declarations: every operation takes
 * one `{ path, body?, query?, headers? }` argument, and list operations
 * answer `{ items, nextPageToken? }`.
 */
export interface PhotonLikeClient {
  projects: {
    // Note the path key: `getProject` wants `projectId`, while the
    // message/user/space operations below want `id`. That asymmetry is the
    // SDK's, not a typo, and photon-contract.ts is what caught it.
    get(input: { path: { projectId: string } }): Promise<unknown>;
    createOrGetUser(input: {
      path: { id: string };
      body: { handle: string; platform: string };
    }): Promise<PhotonUser>;
    listUsers(input: {
      path: { id: string };
      query?: Record<string, unknown>;
    }): Promise<{ items?: PhotonUser[] }>;
    createOrGetSpace(input: {
      path: { id: string };
      headers: { "idempotency-key": string };
      body: { participantIds: string[]; platform: string };
    }): Promise<PhotonSpace>;
    sendMessage(input: {
      path: { id: string };
      headers: { "idempotency-key": string };
      body: {
        spaceId: string;
        senderId: string;
        parentMessageId?: string;
        content: { type: "text"; format: string; text: string };
      };
    }): Promise<PhotonSentMessage | undefined>;
  };
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

/**
 * The handle as Photon wants it: E.164 for phone numbers.
 *
 * `normalizeHandle` is for *comparison* and throws the `+` away, which is the
 * wrong thing to send. A ten-digit number is assumed NANP and gets `+1`,
 * matching the only country Photon's beta assigns lines in; anything already
 * carrying a country code just gets the `+` back. An email (an Apple ID is a
 * valid iMessage handle) is passed through lowercased.
 */
export function toPhotonHandle(handle: string): string {
  const normalized = normalizeHandle(handle);
  if (normalized.includes("@")) return normalized;
  if (normalized.length === 10) return `+1${normalized}`;
  return `+${normalized}`;
}

/** True for a user that is one of our lines rather than a contact. */
function isLine(user: PhotonUser): boolean {
  const type = user?.details?.type;
  return type === "shared" || type === "dedicated";
}

/**
 * Turn a Photon problem response into something a non-technical user can act
 * on, keeping the raw detail for anything unrecognised.
 *
 * The codes come from `@photon-ai/api`'s generated problem types. The first
 * two are the ones that will actually happen during a demo.
 */
function explainError(error: unknown, to: string): string {
  const code = problemCode(error);
  switch (code) {
    case "SHARED_LINE_INBOUND_REQUIRED":
      return (
        `${to} has not texted your Photon number yet. A shared Photon line ` +
        `cannot send first: ask them to text "ping" to it, then try again.`
      );
    case "SHARED_LINE_NOT_ASSIGNED":
      return (
        "no shared iMessage line is assigned to this Photon project. Assign " +
        "one in the Photon dashboard (Platforms -> iMessage)."
      );
    case "SHARED_LINE_GROUP_UNSUPPORTED":
      return "a shared Photon line cannot message a group, only one person.";
    case "SENDER_NOT_AUTHORIZED":
    case "SENDER_NOT_FOUND":
      return (
        "Photon rejected the sending line. Check PHOTON_SENDER_ID, or clear " +
        "it to let the service discover the line itself."
      );
    case "NOT_AUTHENTICATED":
      return "Photon rejected the API key. Check PHOTON_API_KEY.";
    case "INSUFFICIENT_SCOPE":
      return (
        "the API key lacks a scope it needs. It wants project:read and " +
        "project:write."
      );
    case "BILLING_SUSPENDED":
      return "the Photon account is out of credits.";
    case "USER_IDENTITY_CONFLICT":
      return `Photon could not resolve ${to} to a single iMessage identity.`;
    default:
      return describe(error);
  }
}

/** Read a problem `code` off an ApiError-shaped error, if there is one. */
function problemCode(error: unknown): string | null {
  const body = (error as { body?: unknown })?.body;
  const fromBody = (body as { code?: unknown })?.code;
  if (typeof fromBody === "string") return fromBody;
  const direct = (error as { code?: unknown })?.code;
  return typeof direct === "string" ? direct : null;
}

export function createPhotonTransport(config: PhotonConfig): MessageTransport {
  const { apiKey, projectId } = config;
  const clientFactory = config.clientFactory ?? defaultClientFactory(apiKey);
  let client: PhotonLikeClient | null = null;
  let cachedSenderId: string | null = config.senderId ?? null;

  async function getClient(): Promise<PhotonLikeClient> {
    if (client === null) client = await clientFactory();
    return client;
  }

  /**
   * Find our own line's user id.
   *
   * Sending needs the *line* as `senderId`, not the recipient. The project's
   * users are its contacts plus its lines, and only a line carries
   * `details.type`. Cached: it does not change while the service runs.
   */
  async function resolveSenderId(): Promise<string> {
    if (cachedSenderId) return cachedSenderId;
    const photon = await getClient();
    const response = await photon.projects.listUsers({
      path: { id: projectId },
      query: { platform: PLATFORM },
    });
    // A line with no id is unusable as a sender, so it is not a candidate.
    const lines = (response?.items ?? []).filter((user) => isLine(user) && user.id);
    if (lines.length === 0) {
      throw new Error(
        "no iMessage line found on this Photon project. Assign a shared line " +
          "in the Photon dashboard, or set PHOTON_SENDER_ID.",
      );
    }
    if (lines.length > 1 && !config.senderId) {
      // Picking one at random would send from a number the contact has never
      // seen, and a shared line only accepts replies on the line they texted.
      throw new Error(
        `this project has ${lines.length} iMessage lines ` +
          `(${lines.map((line) => line.handle ?? line.id).join(", ")}). ` +
          "Set PHOTON_SENDER_ID to the one your contacts text.",
      );
    }
    // Non-null: the filter above kept only lines that have an id.
    cachedSenderId = lines[0].id as string;
    return cachedSenderId;
  }

  /**
   * Resolve `to` to the space to send into.
   *
   * `createOrGetUser` + `createOrGetSpace` are both idempotent, so this is
   * safe to repeat and does not depend on scanning message history. On a
   * shared line the space only becomes sendable once the contact has texted
   * in, which Photon reports as SHARED_LINE_INBOUND_REQUIRED.
   */
  async function resolveSpace(to: string, senderId: string): Promise<string> {
    const photon = await getClient();
    const recipient = await photon.projects.createOrGetUser({
      path: { id: projectId },
      body: { handle: toPhotonHandle(to), platform: PLATFORM },
    });
    if (!recipient?.id) throw new Error(`Photon returned no user id for ${to}`);

    const space = await photon.projects.createOrGetSpace({
      path: { id: projectId },
      headers: { "idempotency-key": idempotencyKey("space", `${senderId}:${recipient.id}`) },
      body: { participantIds: [senderId, recipient.id], platform: PLATFORM },
    });
    if (!space?.id) throw new Error(`Photon returned no conversation id for ${to}`);
    return space.id;
  }

  return {
    mode: "live",

    async send(to, text) {
      let senderId: string;
      let spaceId: string;
      try {
        senderId = await resolveSenderId();
        spaceId = await resolveSpace(to, senderId);
      } catch (error) {
        return { ok: false, error: explainError(error, to) };
      }

      try {
        const photon = await getClient();
        const result = await photon.projects.sendMessage({
          path: { id: projectId },
          // Retries must not duplicate a message the user already sent.
          headers: { "idempotency-key": idempotencyKey(to, text) },
          body: {
            spaceId,
            senderId,
            // `format` is a free string in the SDK's types; "plain" is the
            // one guess left in this file. If Photon rejects it, this is the
            // line to change.
            content: { type: "text", format: "plain", text },
          },
        });
        // Delivery is accepted asynchronously, so "ok" here means accepted
        // for dispatch, not confirmed delivered.
        return { ok: true, messageId: result?.id };
      } catch (error) {
        return { ok: false, error: explainError(error, to) };
      }
    },

    async health() {
      if (!apiKey || !projectId) {
        return { ok: false, detail: "PHOTON_API_KEY or PHOTON_PROJECT_ID is not set" };
      }
      try {
        const photon = await getClient();
        await photon.projects.get({ path: { projectId } });
      } catch (error) {
        return { ok: false, detail: `Photon API unreachable: ${describe(error)}` };
      }
      // Reachable but with no line assigned, nothing can be sent, so that is
      // not healthy — and it is a setup mistake worth naming before a demo.
      try {
        const senderId = await resolveSenderId();
        return { ok: true, detail: `Photon API reachable; sending line ${senderId}` };
      } catch (error) {
        return { ok: false, detail: describe(error) };
      }
    },
  };
}

function defaultClientFactory(apiKey: string): () => Promise<PhotonLikeClient> {
  return async () => {
    let module: Record<string, unknown>;
    try {
      // Dynamic so the service starts, and runs in stub mode, with no deps
      // installed. Package: @photon-ai/api (verified present on npm).
      module = (await import("@photon-ai/api")) as Record<string, unknown>;
    } catch {
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
