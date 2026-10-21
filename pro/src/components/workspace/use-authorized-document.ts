import { useEffect, useState } from "react";
import type { WorkspaceRepository } from "@/domain/workspace";

export function useAuthorizedDocument(
  id: string,
  repository: WorkspaceRepository,
  attempt: number,
) {
  const [state, setState] = useState<{
    url?: string;
    blob?: Blob;
    error?: string;
  }>({});
  useEffect(() => {
    const abort = new AbortController();
    let url: string | undefined;
    void (async () => {
      try {
        if (!repository.loadDocument) throw new Error("Documento indisponível");
        const blob = await repository.loadDocument(id, abort.signal);
        if (abort.signal.aborted) return;
        url = URL.createObjectURL(blob);
        setState({ url, blob });
      } catch {
        if (!abort.signal.aborted)
          setState({
            error:
              "Não foi possível carregar o PDF. Tente novamente; suas edições continuam na tela.",
          });
      }
    })();
    return () => {
      abort.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [id, repository, attempt]);
  return state;
}
