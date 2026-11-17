import { expect, it } from "vitest";
import { isFiscalInvoiceFailure } from "./conversion-failure";

it.each([
  ["fiscal_invoice", "Nota identificada.", true],
  [
    "UnsupportedDocumentContentError",
    "Este arquivo é uma nota fiscal eletrônica (NF-e ou NFS-e), não um extrato bancário.",
    true,
  ],
  [
    "unsupported_document_type",
    "Este arquivo é uma nota fiscal eletrônica (NF-e ou NFS-e).",
    true,
  ],
  ["UnsupportedDocumentContentError", "Relatório de investimentos.", false],
  [
    "InternalError",
    "Este arquivo é uma nota fiscal eletrônica (NF-e ou NFS-e).",
    false,
  ],
  [undefined, undefined, false],
])(
  "reconhece nota fiscal em lotes antigos e novos: %s",
  (code, message, expected) => {
    expect(isFiscalInvoiceFailure(code, message)).toBe(expected);
  },
);
