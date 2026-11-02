"use client";
import { useEffect, useRef, useState } from "react";
import type { WorkspaceController } from "./use-workspace-controller";

export function ConflictRecovery({
  controller,
}: {
  controller: WorkspaceController;
}) {
  const [comparison, setComparison] = useState<Awaited<
    ReturnType<WorkspaceController["compareConflict"]>
  > | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (comparison) dialog.current?.showModal();
  }, [comparison]);
  return (
    <>
      <button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            setComparison(await controller.compareConflict());
            setError(null);
          } catch (cause) {
            setError(
              cause instanceof Error
                ? cause.message
                : "Falha ao carregar a revisão.",
            );
          } finally {
            setBusy(false);
          }
        }}
      >
        Comparar revisão e rascunho
      </button>
      {error && <span role="alert">{error}</span>}
      {comparison && (
        <dialog
          ref={dialog}
          onCancel={() => setComparison(null)}
          aria-label="Comparar revisão"
          style={{
            border: "1px solid #d6dbe1",
            borderRadius: 12,
            padding: 24,
            maxWidth: "90vw",
          }}
        >
          <section
            className="modal-card"
            style={{ maxHeight: "80vh", overflow: "auto", maxWidth: "90vw" }}
          >
            <h2>Comparar revisão</h2>
            <p>
              Confira os valores confirmados e seu rascunho. Reaplicar envia
              apenas suas alterações locais sobre esta revisão. Uma nova
              alteração simultânea será verificada novamente.
            </p>
            <table>
              <thead>
                <tr>
                  <th>Rascunho local</th>
                  <th>Revisão confirmada</th>
                </tr>
              </thead>
              <tbody>
                {comparison.draft.map((local) => {
                  const latest = comparison.latest.find(
                    (row) => row.id === local.id,
                  );
                  const render = (row: typeof local) =>
                    `${row.date} · ${row.description} · crédito ${row.credit ?? 0} · débito ${row.debit ?? 0}${row.isDeleted ? " · excluído" : ""}`;
                  return (
                    <tr key={local.id}>
                      <td>{render(local)}</td>
                      <td>{latest ? render(latest) : "Inclusão local"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <button
              onClick={() => {
                controller.recoverConflict(comparison);
                setComparison(null);
              }}
            >
              Reaplicar minhas alterações
            </button>
            <button onClick={() => setComparison(null)}>
              Voltar ao rascunho
            </button>
          </section>
        </dialog>
      )}
    </>
  );
}
