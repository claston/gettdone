"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  createOrganizationService,
  type OrganizationClient,
} from "@/data/organization-service";
import { loadFinancialMonth } from "@/data/financial-center-service";
import { uploadFiscalNote } from "@/data/fiscal-service";

export function FiscalConversionImport({
  conversionId,
  filename,
  loadDocument,
}: {
  conversionId: string;
  filename: string;
  loadDocument?: (id: string) => Promise<Blob>;
}) {
  const [clients, setClients] = useState<OrganizationClient[] | null>(null);
  const [clientId, setClientId] = useState("");
  const [capability, setCapability] = useState<{
    client: string;
    enabled: boolean;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [imported, setImported] = useState(false);
  const submitting = useRef(false);
  useEffect(() => {
    const abort = new AbortController();
    createOrganizationService()
      .catalog(abort.signal)
      .then((catalog) => {
        if (!abort.signal.aborted) setClients(catalog.clients);
      })
      .catch((caught) => {
        if (!abort.signal.aborted)
          setError(
            caught instanceof Error
              ? caught.message
              : "Não foi possível consultar as empresas.",
          );
      });
    return () => abort.abort();
  }, []);
  useEffect(() => {
    if (!clientId) return;
    const abort = new AbortController();
    loadFinancialMonth(clientId, "limit=1", abort.signal)
      .then((month) => {
        if (!abort.signal.aborted)
          setCapability({
            client: clientId,
            enabled: month.features?.invoices === true,
          });
      })
      .catch((caught) => {
        if (!abort.signal.aborted)
          setError(
            caught instanceof Error
              ? caught.message
              : "Não foi possível consultar a gestão fiscal.",
          );
      });
    return () => abort.abort();
  }, [clientId]);
  const checked = capability?.client === clientId;
  async function submit() {
    if (
      !loadDocument ||
      !checked ||
      !capability?.enabled ||
      submitting.current ||
      imported
    )
      return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      const blob = await loadDocument(conversionId);
      await uploadFiscalNote(
        clientId,
        new File([blob], filename, { type: "application/pdf" }),
      );
      setImported(true);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível importar a nota. Tente novamente.",
      );
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <div className="fiscal-conversion-import">
      <h3>Nota fiscal identificada</h3>
      <p>
        Confira o PDF original e escolha a empresa da nota. A importação não
        cria nem associa lançamentos bancários.
      </p>
      {error && <p role="alert">{error}</p>}
      {clients === null && !error && <p role="status">Consultando empresas…</p>}
      {clients && clients.length === 0 && (
        <p>
          Cadastre a empresa em <Link href="/clients">Empresas</Link> para
          importar a nota.
        </p>
      )}
      {clients && clients.length > 0 && (
        <label>
          Empresa da nota fiscal
          <select
            aria-label="Empresa da nota fiscal"
            value={clientId}
            disabled={busy || imported}
            onChange={(event) => {
              setClientId(event.target.value);
              setCapability(null);
              setError(null);
            }}
          >
            <option value="">Selecione a empresa</option>
            {clients.map((client) => (
              <option key={client.id} value={client.id}>
                {client.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {clientId && !checked && !error && (
        <p role="status">Consultando disponibilidade…</p>
      )}
      {checked && !capability.enabled && (
        <p>
          A gestão fiscal ainda não está habilitada. A nota permanece disponível
          no lote.
        </p>
      )}
      {checked && capability.enabled && !imported && (
        <button
          className="primary-button"
          disabled={busy || !loadDocument}
          onClick={() => void submit()}
        >
          {busy ? "Importando nota…" : "Importar na gestão fiscal"}
        </button>
      )}
      {imported && (
        <p role="status">
          Importação da nota iniciada. Acompanhe a leitura em{" "}
          <Link
            href={`/financial/${encodeURIComponent(clientId)}?tab=${encodeURIComponent("Conciliação")}`}
          >
            Gestão fiscal
          </Link>
          .
        </p>
      )}
    </div>
  );
}
