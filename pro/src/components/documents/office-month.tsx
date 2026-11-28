"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { documentsService, type DocumentItem } from "@/data/documents-service";
import { MonthFilter } from "./month-filter";
import styles from "./month-filter.module.css";

export function useOfficeMonth(month: string, attempt: number) {
  const [state, setState] = useState<{
    month: string;
    items: DocumentItem[];
    partial: boolean;
    error?: string;
  } | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    void (async () => {
      const items = new Map<string, DocumentItem>();
      let cursor = "";
      try {
        for (let page = 0; page < 20; page++) {
          const query = new URLSearchParams({
            month,
            limit: "50",
            ...(cursor ? { cursor } : {}),
          });
          const result = await documentsService.list(
            query.toString(),
            abort.signal,
          );
          for (const item of result.items) items.set(item.job_id, item);
          cursor = result.next_cursor ?? "";
          if (!cursor) break;
        }
        if (!abort.signal.aborted)
          setState({
            month,
            items: [...items.values()],
            partial: Boolean(cursor),
          });
      } catch {
        if (!abort.signal.aborted)
          setState({
            month,
            items: [],
            partial: false,
            error:
              "Não foi possível consultar os documentos deste mês. Abra Documentos para tentar novamente.",
          });
      }
    })();
    return () => abort.abort();
  }, [month, attempt]);
  return state?.month === month ? state : null;
}

export function OfficeMonthFilter({ month }: { month: string }) {
  const router = useRouter();
  return (
    <div>
      <MonthFilter
        month={month}
        onChange={(value) =>
          router.push(`/dashboard?month=${encodeURIComponent(value)}`)
        }
        allHref="/dashboard"
        unknownHref="/dashboard?month=unknown"
      />
      <p className={styles.hint}>
        O mês organiza documentos pelas datas identificadas. Não comprova que o
        mês está completo.
      </p>
    </div>
  );
}

export function OfficeClientCounts({ items }: { items: DocumentItem[] }) {
  const reviewed = items.filter((i) => i.bank_reviewed).length;
  const pending = items.filter((i) =>
    i.is_bank ? !i.bank_reviewed : i.verification?.status !== "verified",
  ).length;
  const pieces = [
    items.length === 1
      ? "1 documento disponível"
      : `${items.length} documentos disponíveis`,
  ];
  if (reviewed)
    pieces.push(
      reviewed === 1 ? "1 extrato revisado" : `${reviewed} extratos revisados`,
    );
  if (pending) pieces.push(`${pending} a conferir`);
  return <p>{pieces.join(" · ")}</p>;
}
