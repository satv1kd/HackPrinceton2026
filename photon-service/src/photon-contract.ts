/**
 * Compile-time proof that `PhotonLikeClient` matches the real SDK.
 *
 * `transport.ts` talks to Photon through a hand-written interface so the
 * service can run, and be tested, with no SDK installed. The risk in that is
 * obvious: the hand-written shape can drift from the real one, and nothing
 * would notice until a live send failed. This file closes that gap without
 * needing an account or a network call — if the declarations published in
 * `@photon-ai/api` stop satisfying our interface, `npm run typecheck` fails.
 *
 * It is types only. Nothing here is imported at runtime, so the service still
 * starts in stub mode with no dependencies installed. `npm run typecheck`
 * does need `npm install` (for `typescript` itself as much as for this).
 *
 * What this does and does not prove:
 *
 *  - PROVES the namespace, argument envelopes, required headers and response
 *    envelopes in `transport.ts` are the ones the SDK declares.
 *  - DOES NOT prove the live API behaves as its own types claim, nor that
 *    `content.format: "plain"` is a value Photon accepts. Only a real send
 *    settles those.
 */

import type { PhotonNamespaces } from "@photon-ai/api";

import type { PhotonLikeClient } from "./transport.ts";

/** The real client, reduced to the namespace this service uses. */
type RealClient = { projects: PhotonNamespaces["projects"] };

/**
 * The assignment is the assertion: a real client has to be usable wherever
 * `PhotonLikeClient` is expected, which is exactly what `transport.ts`
 * assumes when it casts the SDK's constructor to that interface.
 */
declare const realClient: RealClient;
export const contractHolds: PhotonLikeClient = realClient;

/**
 * The arguments `transport.ts` actually passes, typed as the SDK's own input
 * types.
 *
 * The assignment above is necessary but not sufficient: TypeScript compares
 * method parameters bivariantly, so an interface with a loose argument type
 * stays assignable to one with a strict type. These literals are the real
 * check — each is exactly what `send()` and `health()` build, and the SDK's
 * input schemas are `$strict`, so a misspelled or surplus field fails here.
 */
type Project = RealClient["projects"];
type Input<K extends keyof Project> = Project[K] extends (input: infer I, ...rest: never[]) => unknown
  ? I
  : never;

export const getProjectCall: Input<"get"> = {
  path: { projectId: "proj-1" },
};

export const createOrGetUserCall: Input<"createOrGetUser"> = {
  path: { id: "proj-1" },
  body: { handle: "+14155550123", platform: "imessage" },
};

export const listUsersCall: Input<"listUsers"> = {
  path: { id: "proj-1" },
  query: { platform: "imessage" },
};

export const createOrGetSpaceCall: Input<"createOrGetSpace"> = {
  path: { id: "proj-1" },
  headers: { "idempotency-key": "heard-space-1" },
  body: { participantIds: ["line-1", "user-1"], platform: "imessage" },
};

export const sendMessageCall: Input<"sendMessage"> = {
  path: { id: "proj-1" },
  headers: { "idempotency-key": "heard-1" },
  body: {
    spaceId: "space-1",
    senderId: "line-1",
    content: { type: "text", format: "plain", text: "I want water" },
  },
};
