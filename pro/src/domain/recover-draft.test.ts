import { expect, it } from "vitest";
import { recoverDraft } from "./recover-draft";
import type { Transaction } from "./workspace";

it("reapplies local fields while preserving another editor's confirmed amount and inserted rows", () => {
  const original = {
    id: "stable",
    description: "Original",
    credit: 10,
    debit: null,
  } as Transaction;
  const current = { ...original, credit: 20 };
  const inserted = { ...original, id: "other" };
  expect(
    recoverDraft(
      [original],
      [current, inserted],
      [{ ...original, description: "Local", documentNumber: "000ABC" }],
    ),
  ).toEqual([{ ...current, description: "Local", documentNumber: "000ABC" }, inserted]);
});

it("does not duplicate an insertion committed before a lost response", () => {
  const inserted = { id: "manual-stable", description: "Incluído", credit: 10, debit: null } as Transaction;
  const recovered = recoverDraft([], [inserted], [{ ...inserted, description: "Rascunho posterior" }]);
  expect(recovered).toHaveLength(1);
  expect(recovered[0].description).toBe("Rascunho posterior");
});
