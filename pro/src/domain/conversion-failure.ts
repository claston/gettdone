/** Support both new classified failures and batches created before that code existed. */
export function isFiscalInvoiceFailure(
  code?: string | null,
  message?: string | null,
): boolean {
  return (
    code === "fiscal_invoice" ||
    (["UnsupportedDocumentContentError", "unsupported_document_type"].includes(
      code ?? "",
    ) &&
      Boolean(
        message?.startsWith(
          "Este arquivo é uma nota fiscal eletrônica (NF-e ou NFS-e)",
        ),
      ))
  );
}
