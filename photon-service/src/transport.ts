/**
 * Message transports for the Photon relay (Task 12).
 *
 * IMPORTANT - the PRD is wrong about Photon, and this file is where that
 * shows. The PRD says "a small TypeScript service runs on a Mac signed into
 * Messages and uses Photon's local iMessage SDK". Photon is not that: it is a
 * **hosted** platform that owns the phone lines. Nothing runs against the
 * Messages app, and no Mac is needed.
 *
 * The consequence that changes the product, not just this file: a shared
 * Photon line **cannot send an unsolicited first message**. The recipient has
 * to message the assigned number first (send exactly `ping` as an iMessage).
 * Until they do, there is no conversation to send into. "Send to Mom"
 * therefore only works for contacts who have texted the line.
 *
 * That is surfaced to the user as an ordinary send failure with a readable
 * reason, which is what `static/js/send-buttons.js` renders.
 *
 * ## Which Photon API this talks to
 *
 * Photon exposes two of them, and they take different credentials:
 *
 * - `@photon-ai/api` - the account/organization REST API. Wants a key that
 *   looks like `pho_sk_…` or `pho_ask_…`, issued from an account that has API
 *   keys to give. This service was written against it first; with a Spectrum
 *   project secret every call answers `403 FORBIDDEN`.
 * - `spectrum-ts` - the Spectrum SDK, which takes `projectId` +
 *   `projectSecret` (the two values the dashboard shows, and the two the
 *   `bun create spectrum-project` scaffold writes into a `.env`) and
 *   exchanges them for tokens itself. That is what this file uses now.
 *
 * Sending is `space.create(handle)` then `space.send(text)`:
 * `SpaceNamespace.create` resolves *or creates* the 1:1 conversation from the
 * participant, and the provider's own types say a user id is "the canonical
 * address (E.164 phone or email)" - the format `toPhotonHandle` produces.
 *
 * Spectrum is built around a long-running agent loop that reads
 * `app.messages`. This relay never reads it: it holds one app open for the
 * process's life and sends into it per request. Nothing in the SDK forbids
 * that, but it is not the shape the SDK's examples use.
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
  /** Release anything long-lived. Safe to call more than once. */
  stop?(): Promise<void>;
}

/* ------------------------------------------------------------------ stub */

/**
 * Logs instead of sending.
 *
 * This is the default when no credentials are configured, and it is what
 * makes the rest of the system demoable with no Photon account: the UI
 * exercises the full confirm -> send -> sent path and the text lands in the
 * log.
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

/* -------------------------------------------------------------- spectrum */

export interface SpectrumConfig {
  projectId: string;
  projectSecret: string;
  /** Injectable for tests; defaults to the real SDK via dynamic import. */
  appFactory?: () => Promise<SpectrumLikeApp>;
  /** Injectable for tests; defaults to the SDK's `cloud` helpers. */
  cloudFactory?: () => Promise<SpectrumLikeCloud>;
}

/** A conversation. Narrow view of the SDK's `PlatformSpace`. */
export interface SpectrumLikeSpace {
  id?: string;
  send(text: string): Promise<{ id?: string } | undefined>;
}

/**
 * The slice of the Spectrum SDK this service uses: the iMessage platform
 * instance's space namespace, plus a way to shut the app down.
 */
export interface SpectrumLikeApp {
  space: {
    create(users: string | string[]): Promise<SpectrumLikeSpace>;
  };
  stop?(): Promise<void>;
}

/**
 * The project reads used for health. Both come from the SDK's `cloud`, and
 * both take the secret.
 *
 * `cloud.getImessageInfo(projectId)` and `getSubscription(projectId)` look
 * like better fits and are not: they take no secret and answer
 * `401 Missing authorization header`, so they are for a signed-in dashboard
 * session, not for a project secret. `issueImessageTokens` is the call that
 * both proves the secret works and names the line type.
 */
export interface SpectrumLikeCloud {
  getProject(
    projectId: string,
    projectSecret: string,
  ): Promise<{ name?: string; profile?: { imessageSynced?: boolean } } | undefined>;
  issueImessageTokens?(
    projectId: string,
    projectSecret: string,
  ): Promise<{ type?: string } | undefined>;
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
 * the platform reports `+1 (415) 555-0123`, and those must be the same person
 * or the send fails for no visible reason. For numbers longer than ten digits
 * the last ten decide it, which is correct for NANP and a deliberate
 * approximation elsewhere - an international number that collides in its last
 * ten digits would be confused, which is far less likely than the
 * country-code mismatch this fixes.
 */
export function handleKey(handle: string): string {
  const normalized = normalizeHandle(handle);
  if (normalized.includes("@")) return normalized;
  return normalized.length > 10 ? normalized.slice(-10) : normalized;
}

/**
 * The handle as the platform wants it: E.164 for phone numbers.
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

/**
 * Turn a failure into something a non-technical user can act on, keeping the
 * raw detail for anything unrecognised.
 *
 * `SpectrumCloudError` carries `status` and `code`; the HTTP status is the
 * part worth trusting, because the code strings are not published anywhere
 * this service can check them against.
 */
export function explainError(error: unknown, to: string): string {
  const code = problemCode(error);
  switch (code) {
    case "SHARED_LINE_INBOUND_REQUIRED":
      return inboundRequired(to);
    case "SHARED_LINE_NOT_ASSIGNED":
      return (
        "no iMessage line is assigned to this Photon project. Assign one in " +
        "the dashboard (Platforms -> iMessage)."
      );
    case "SHARED_LINE_GROUP_UNSUPPORTED":
      return "a shared Photon line cannot message a group, only one person.";
    case "BILLING_SUSPENDED":
      return "the Photon account is out of credits.";
  }
  switch (problemStatus(error)) {
    case 401:
      return "Photon rejected the project secret. Check PHOTON_PROJECT_SECRET.";
    case 403:
      return (
        "Photon refused this project. Usually the secret belongs to a " +
        "different project, or the account has no phone connected yet " +
        "(add one from the avatar menu in the dashboard)."
      );
    case 404:
      return "Photon has no project with this id. Check PHOTON_PROJECT_ID.";
  }
  // The inbound-first rule is the failure most likely to happen in a demo,
  // and it arrives as prose rather than as a code.
  const message = describe(error);
  if (/inbound|has not messaged|no conversation/i.test(message)) return inboundRequired(to);
  // Observed live from `space.send`: "[spectrum-imessage] Target not allowed
  // for this project", before the account had a phone connected. Photon does
  // not say which of the two reasons it is, so the message names both.
  if (/target not allowed/i.test(message)) {
    return (
      `Photon will not send to ${to} yet. Two things have to be true: your ` +
      `Photon account needs a phone connected (avatar menu in the dashboard), ` +
      `and ${to} has to have texted your Photon line "ping" first - a shared ` +
      `line cannot send first.`
    );
  }
  return message;
}

function inboundRequired(to: string): string {
  return (
    `${to} has not texted your Photon number yet. A shared Photon line ` +
    `cannot send first: ask them to text "ping" to it, then try again.`
  );
}

/** Read a problem `code` off an error, if there is one. */
function problemCode(error: unknown): string | null {
  const body = (error as { body?: unknown })?.body;
  const fromBody = (body as { code?: unknown })?.code;
  if (typeof fromBody === "string") return fromBody;
  const direct = (error as { code?: unknown })?.code;
  return typeof direct === "string" ? direct : null;
}

function problemStatus(error: unknown): number | null {
  const direct = (error as { status?: unknown })?.status;
  if (typeof direct === "number") return direct;
  const body = (error as { body?: unknown })?.body;
  const fromBody = (body as { status?: unknown })?.status;
  return typeof fromBody === "number" ? fromBody : null;
}

export function createSpectrumTransport(config: SpectrumConfig): MessageTransport {
  const { projectId, projectSecret } = config;
  const appFactory = config.appFactory ?? defaultAppFactory(projectId, projectSecret);
  const cloudFactory = config.cloudFactory ?? defaultCloudFactory();
  // One app for the process. Held as the promise, not the result, so two
  // sends arriving together start one Spectrum, not two.
  let app: Promise<SpectrumLikeApp> | null = null;

  function getApp(): Promise<SpectrumLikeApp> {
    if (app === null) {
      app = appFactory().catch((error) => {
        // A failed start must not be cached, or every later send repeats the
        // first error long after the cause (a dashboard fix) is gone.
        app = null;
        throw error;
      });
    }
    return app;
  }

  return {
    mode: "live",

    async send(to, text) {
      try {
        const instance = await getApp();
        // `create` resolves an existing 1:1 conversation or opens one; on a
        // shared line, sending into it is what the inbound-first rule gates.
        const space = await instance.space.create(toPhotonHandle(to));
        const message = await space.send(text);
        // Delivery is accepted asynchronously, so "ok" here means accepted
        // for dispatch, not confirmed delivered.
        return { ok: true, messageId: message?.id };
      } catch (error) {
        return { ok: false, error: explainError(error, to) };
      }
    },

    async health() {
      if (!projectId || !projectSecret) {
        return { ok: false, detail: "PHOTON_PROJECT_ID or PHOTON_PROJECT_SECRET is not set" };
      }
      let project: { name?: string; profile?: { imessageSynced?: boolean } } | undefined;
      try {
        const cloud = await cloudFactory();
        project = await cloud.getProject(projectId, projectSecret);
        // Reachable with no iMessage line is not healthy: nothing can be
        // sent, and it is a setup mistake worth naming before a demo.
        if (cloud.issueImessageTokens) {
          const tokens = await cloud.issueImessageTokens(projectId, projectSecret);
          const kind = tokens?.type;
          if (!kind) {
            return {
              ok: false,
              detail:
                "no iMessage line on this Photon project. Assign one in the " +
                "dashboard (Platforms -> iMessage).",
            };
          }
          // `profile.imessageSynced` is NOT a send gate: a real send went
          // through with it false. It is reported, never acted on - health
          // must not refuse a setup that works.
          const synced = project?.profile?.imessageSynced === false ? "; profile not synced" : "";
          return {
            ok: true,
            detail: `Photon reachable; ${kind} iMessage line on ${project?.name ?? projectId}${synced}`,
          };
        }
      } catch (error) {
        return { ok: false, detail: `Photon unreachable: ${explainError(error, "this project")}` };
      }
      return { ok: true, detail: `Photon reachable; project ${project?.name ?? projectId}` };
    },

    async stop() {
      const started = app;
      app = null;
      if (!started) return;
      try {
        await (await started).stop?.();
      } catch (error) {
        console.warn("[photon] could not stop the Spectrum app:", describe(error));
      }
    },
  };
}

/**
 * Build the real Spectrum app: one project, the iMessage provider, and the
 * platform instance's space namespace.
 *
 * Imported dynamically so the service starts, and runs in stub mode, with no
 * dependencies installed.
 */
function defaultAppFactory(projectId: string, projectSecret: string): () => Promise<SpectrumLikeApp> {
  return async () => {
    const { Spectrum, imessage } = await loadSpectrum();
    const app = await Spectrum({
      projectId,
      projectSecret,
      providers: [imessage.config()],
    });
    const instance = imessage(app);
    return {
      space: {
        create: (users) => instance.space.create(users) as Promise<SpectrumLikeSpace>,
      },
      stop: () => app.stop(),
    };
  };
}

function defaultCloudFactory(): () => Promise<SpectrumLikeCloud> {
  return async () => (await loadSpectrum()).cloud as SpectrumLikeCloud;
}

interface SpectrumModule {
  Spectrum: (options: {
    projectId: string;
    projectSecret: string;
    providers: unknown[];
  }) => Promise<{ stop(): Promise<void> }>;
  imessage: {
    config(): unknown;
    (app: unknown): { space: { create(users: string | string[]): Promise<unknown> } };
  };
  cloud: unknown;
}

async function loadSpectrum(): Promise<SpectrumModule> {
  let core: Record<string, unknown>;
  let provider: Record<string, unknown>;
  try {
    core = (await import("spectrum-ts")) as Record<string, unknown>;
    provider = (await import("spectrum-ts/providers/imessage")) as Record<string, unknown>;
  } catch (error) {
    throw new Error(
      "spectrum-ts is not installed. Run `npm install` in photon-service/, " +
        `or leave PHOTON_PROJECT_SECRET unset to run in stub mode. (${describe(error)})`,
    );
  }
  const { Spectrum, cloud } = core as unknown as SpectrumModule;
  const imessage = (provider.imessage ?? provider.default) as SpectrumModule["imessage"] | undefined;
  if (typeof Spectrum !== "function" || typeof imessage !== "function") {
    throw new Error("spectrum-ts did not export Spectrum and the imessage provider");
  }
  return { Spectrum, imessage, cloud };
}

function describe(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}
