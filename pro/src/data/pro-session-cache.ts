// Session-scoped memory only: no credentials or document contents in browser storage.
export const PRO_SESSION_EXPIRED_EVENT = "ofx-pro-session-expired";
let version = 0;
let owner: string | null = null;
let verifiedAt = 0;

export const proSessionCacheVersion = () => version;
export const hasVerifiedProSession = () => owner !== null;
export const proSessionVerifiedAt = () => verifiedAt;

export function invalidateProSessionCaches() {
  version++;
}

export function rememberProSession(userId: string) {
  if (owner !== userId) invalidateProSessionCaches();
  owner = userId;
  verifiedAt = Date.now();
}

export function forgetProSession() {
  owner = null;
  verifiedAt = 0;
  invalidateProSessionCaches();
}

export function expireProSession() {
  if (owner === null) return;
  forgetProSession();
  if (typeof window !== "undefined")
    window.dispatchEvent(new Event(PRO_SESSION_EXPIRED_EVENT));
}
