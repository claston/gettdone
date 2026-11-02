import type { Transaction } from "./workspace";

// Reapply only the local changes. Unrelated confirmed fields/rows stay current.
// Conflicting fields are displayed by the caller before this is accepted.
export function recoverDraft(
  baseline: Transaction[],
  latest: Transaction[],
  draft: Transaction[],
): Transaction[] {
  const before = new Map(baseline.map((row) => [row.id, row]));
  const local = new Map(draft.map((row) => [row.id, row]));
  const fields = [
    "date",
    "description",
    "credit",
    "debit",
    "isDeleted",
    "reviewRegions",
  ] as const;
  const result = latest.map((row) => {
    const original = before.get(row.id);
    const changed = local.get(row.id);
    if (!original) {
      return changed
        ? { ...row, ...Object.fromEntries(fields.map((field) => [field, changed[field]])) }
        : row;
    }
    if (!changed) return { ...row, isDeleted: true };
    const patch = Object.fromEntries(
      fields
        .filter(
          (field) =>
            JSON.stringify(original[field]) !== JSON.stringify(changed[field]),
        )
        .map((field) => [field, changed[field]]),
    );
    return { ...row, ...patch };
  });
  const latestIds = new Set(latest.map((row) => row.id));
  return [
    ...result,
    ...draft.filter((row) => !before.has(row.id) && !latestIds.has(row.id)),
  ];
}
