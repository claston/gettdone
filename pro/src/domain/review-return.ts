export function reviewReturnTo(value: unknown): string {
  const fallback = "/conversions?view=batches";
  if (typeof value !== "string" || value.length > 2000) return fallback;
  let url: URL;
  try {
    url = new URL(value, "https://ofx.local");
  } catch {
    return fallback;
  }
  if (url.origin !== "https://ofx.local" || url.pathname !== "/conversions")
    return fallback;
  const query = new URLSearchParams();
  for (const key of ["view", "client", "account", "period", "cursor"]) {
    const item = url.searchParams.get(key);
    if (item) query.set(key, item);
  }
  return `/conversions?${query}`;
}
