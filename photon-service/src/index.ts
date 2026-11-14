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

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

import {
  createPhotonTransport,
  createStubTransport,
  type MessageTransport,
} from "./transport.ts";

const MAX_BODY_BYTES = 64 * 1024;

export function chooseTransport(env: NodeJS.ProcessEnv = process.env): MessageTransport {
  const apiKey = env.PHOTON_API_KEY?.trim() ?? "";
  const projectId = env.PHOTON_PROJECT_ID?.trim() ?? "";
  const forced = env.PHOTON_MODE?.trim().toLowerCase();

  if (forced === "stub") return createStubTransport();
  if (forced === "live" || (apiKey && projectId)) {
    if (!apiKey || !projectId) {
      throw new Error(
        "PHOTON_MODE=live needs both PHOTON_API_KEY and PHOTON_PROJECT_ID",
      );
    }
    return createPhotonTransport({ apiKey, projectId });
  }
  console.log(
    "[photon] no PHOTON_API_KEY/PHOTON_PROJECT_ID; starting in stub mode " +
      "(messages are logged, not sent)",
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
  const transport = chooseTransport(env);
  return createServer(createHandler(transport, env.PHOTON_PROJECT_ID ?? ""));
}

// Only listen when run directly, so tests can import the handler.
if (import.meta.url === `file://${process.argv[1]}`) {
  const port = Number(process.env.PORT ?? 3001);
  createPhotonServer().listen(port, () => {
    console.log(`[photon] relay listening on http://127.0.0.1:${port}`);
  });
}
