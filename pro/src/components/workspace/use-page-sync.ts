import { useEffect, useRef, useState } from "react";

// This hook only follows provenance anchors. It never changes review data.
export function usePageSync(
  page: number | null,
  targetId: string | undefined,
  follow: (id: string) => void,
  enabled = true,
) {
  const [editing, setEditing] = useState(false);
  const last = useRef<string | null>(null);
  useEffect(() => {
    if (editing) return;
    if (!enabled) {
      last.current = null;
      return;
    }
    if (page === null || !targetId) {
      last.current = null;
      return;
    }
    const key = `${page}:${targetId}`;
    if (last.current === key) return;
    last.current = key;
    follow(targetId);
  }, [editing, page, targetId, follow, enabled]);
  return {
    setEditing,
    editing,
  };
}
