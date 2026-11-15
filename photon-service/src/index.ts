/**
 * Photon relay (Task 12).
 *
 * A tiny HTTP service that FastAPI forwards sends to. Its contract is the
 * one thing the rest of the system depends on:
 *
 *   GET  /health          -> {status, mode, imessage_connected, project_id?}
 *   POST /send {to, text} -> {ok, error?, messageId?}
 *
 * Everything Photon-specific is in transport.ts, so if the SDK changes shape
 * nothing outside that file moves.
 *
 * Zero dependencies on purpose: Node's own http server and Node 24's native
 * TypeScript support mean `node src/index.ts` runs with no `npm install`,
 * and the service works in stub mode before anyone has a Photon account.
 */

import { readFileSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createSpectrumTransport,
  createStubTransport,
  type MessageTransport,
} from "./transport.ts";

const MAX_BODY_BYTES = 64 * 1024;
const ENV_FILE = resolve(dirname(fileURLToPath(import.meta.url)), "../../.env");

/**
 * Read the project's own `.env` into `process.env`.
 *
 * The relay is started by `run.sh` and `make photon`, neither of which is a
 * shell the user has exported anything into, so without this the credentials
 * would have to be set in a second place to the one `.env.example` documents.
 * Values already in the environment win, so `PHOTON_MODE=stub make photon`
 * still does what it says. A missing or unreadable file is not an error: no
 * credentials means stub mode, which is a perfectly good way to run.
 */
export function loadEnvFile(path = ENV_FILE, env: NodeJS.ProcessEnv = process.env): void {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return;
  }
  for (const line of text.split(/\r?\n/)) {
    const match = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (!match || line.trimStart().startsWith("#")) continue;
    const [, key, rawValue] = match;
    if (key in env) continue;
    // Strip one layer of matching quotes, then a trailing comment on bare values.
    let value = rawValue.trim();
    const quoted = /^(["'])([\s\S]*)\1\s*(?:#.*)?$/.exec(value);
    value = quoted ? quoted[2] : value.replace(/\s+#.*$/, "").trim();
    env[key] = value;
  }
}

/**
 * The project id and secret, under any of the names they arrive with.
 *
 * `PHOTON_*` is what this project documents, `PROJECT_ID`/`PROJECT_SECRET` is
 * what Photon's own `bun create spectrum-project` scaffold writes, and
 * `PHOTON_API_KEY` is what this service asked for while it was built against
 * the other Photon API - all three name the same two values, and a person
 * who pasted their secret once should not have to paste it again.
 */
export function readCredentials(env: NodeJS.ProcessEnv = process.env): {
  projectId: string;
  projectSecret: string;
} {
  const first = (...names: string[]): string => {
    for (const name of names) {
      const value = env[name]?.trim();
      if (value) return value;
    }
    return "";
  };
  return {
    projectId: first("PHOTON_PROJECT_ID", "PROJECT_ID"),
    projectSecret: first("PHOTON_PROJECT_SECRET", "PROJECT_SECRET", "PHOTON_API_KEY"),
  };
}

export function chooseTransport(env: NodeJS.ProcessEnv = process.env): MessageTransport {
  const { projectId, projectSecret } = readCredentials(env);
  const forced = env.PHOTON_MODE?.trim().toLowerCase();

  if (forced === "stub") return createStubTransport();
  if (forced === "live" || (projectSecret && projectId)) {
    if (!projectSecret || !projectId) {
      throw new Error(
        "PHOTON_MODE=live needs both PHOTON_PROJECT_ID and PHOTON_PROJECT_SECRET",
      );
    }
    return createSpectrumTransport({ projectId, projectSecret });
  }
  console.log(
    "[photon] no PHOTON_PROJECT_ID/PHOTON_PROJECT_SECRET; starting in stub " +
      "mode (messages are logged, not sent)",
  );
  return createStubTransport();
}

function sendJSON(response: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  response.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
  });
  response.end(payload);
}

function readBody(request: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    request.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error("request body too large"));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

export function createHandler(transport: MessageTransport, projectId = "") {
  return async function handle(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    const url = new URL(request.url ?? "/", "http://localhost");

    if (request.method === "GET" && url.pathname === "/health") {
      const health = await transport.health();
      sendJSON(response, health.ok ? 200 : 503, {
        status: health.ok ? "ok" : "unavailable",
        mode: transport.mode,
        imessage_connected: health.ok,
        detail: health.detail,
        ...(projectId ? { project_id: projectId } : {}),
      });
      return;
    }

    if (request.method === "POST" && url.pathname === "/send") {
      let parsed: unknown;
      try {
        parsed = JSON.parse(await readBody(request));
      } catch {
        sendJSON(response, 400, { ok: false, error: "body must be JSON" });
        return;
      }

      const body = (parsed ?? {}) as { to?: unknown; text?: unknown };
      const to = typeof body.to === "string" ? body.to.trim() : "";
      const text = typeof body.text === "string" ? body.text.trim() : "";
      if (!to || !text) {
        sendJSON(response, 400, { ok: false, error: "both 'to' and 'text' are required" });
        return;
      }

      const result = await transport.send(to, text);
      // Always 200 for an attempted send: the {ok:false, error} body is what
      // FastAPI and the UI read, and an HTTP error would lose the reason.
      sendJSON(response, 200, result);
      return;
    }

    sendJSON(response, 404, { ok: false, error: `no route for ${request.method} ${url.pathname}` });
  };
}

export function createPhotonServer(env: NodeJS.ProcessEnv = process.env) {
  if (env === process.env) loadEnvFile();
  const transport = chooseTransport(env);
  return createServer(createHandler(transport, readCredentials(env).projectId));
}

// Only listen when run directly, so tests can import the handler.
if (import.meta.url === `file://${process.argv[1]}`) {
  const port = Number(process.env.PORT ?? 3001);
  createPhotonServer().listen(port, () => {
    console.log(`[photon] relay listening on http://127.0.0.1:${port}`);
  });
}
