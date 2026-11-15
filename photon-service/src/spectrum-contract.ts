/**
 * Compile-time proof that transport.ts matches the real Spectrum SDK.
 *
 * Nothing here runs. `npm run typecheck` fails if `spectrum-ts` changes shape
 * under us, or if the narrow interfaces in transport.ts drift from what the
 * SDK actually provides - which is the only check available without a live
 * account, and the one that caught the previous version of this service
 * talking to the wrong Photon API entirely.
 *
 * Kept out of transport.ts so that file still runs with nothing installed.
 */

import { Spectrum, cloud } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";

import type { SpectrumLikeApp, SpectrumLikeCloud, SpectrumLikeSpace } from "./transport.ts";

/** The send path, exactly as `defaultAppFactory` builds it. */
export async function contractHolds(
  projectId: string,
  projectSecret: string,
): Promise<SpectrumLikeApp> {
  const app = await Spectrum({
    projectId,
    projectSecret,
    providers: [imessage.config()],
  });
  const instance = imessage(app);

  // `space.create` takes the canonical address - the provider's own types say
  // "E.164 phone or email", which is what toPhotonHandle produces.
  const space: SpectrumLikeSpace = await instance.space.create("+15551234567");
  const message = await space.send("I want water");
  const messageId: string | undefined = message?.id;
  void messageId;

  return {
    space: { create: (users) => instance.space.create(users) },
    stop: () => app.stop(),
  };
}

/** The health path: both probes exist and take (projectId, projectSecret). */
export const cloudHolds: SpectrumLikeCloud = cloud;
