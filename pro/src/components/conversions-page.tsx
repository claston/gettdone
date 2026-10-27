"use client";

import { useCallback, useState } from "react";
import { DashboardPage } from "./dashboard-page";
import { NewConversionForm } from "./new-conversion-form";
import { OrganizationPage } from "./organization/organization-page";
import type { OrganizationFilters } from "@/data/organization-service";
import type { OrganizationService } from "@/data/organization-service";
import type { BatchHistoryService } from "@/data/batch-history-service";

export function ConversionsPage({
  initialView = "batches",
  initialFilters,
  initialCursor,
  organization,
  batches,
  workId,
}: {
  initialView?: "batches" | "files";
  initialFilters?: OrganizationFilters;
  initialCursor?: string;
  organization?: OrganizationService;
  batches?: BatchHistoryService;
  workId?: string;
}) {
  const [view, setView] = useState(initialView);
  const [filesVisited, setFilesVisited] = useState(initialView === "files");
  const updateContext = useCallback(
    (url: string) => {
      if (view === "files")
        window.history.replaceState(
          null,
          "",
          `${url}${workId ? `&work=${encodeURIComponent(workId)}` : ""}`,
        );
    },
    [view, workId],
  );
  return (
    <>
      <NewConversionForm workId={workId} />
      <nav
        className="standard-page conversion-views"
        aria-label="Visões de conversões"
      >
        {(
          [
            ["batches", "Lotes"],
            ["files", "Todos os arquivos"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={view === key}
            className={view === key ? "primary-button" : "secondary-button"}
            onClick={() => {
              setView(key);
              if (key === "files") setFilesVisited(true);
              window.history.replaceState(
                null,
                "",
                `/conversions?view=${key}${workId ? `&work=${encodeURIComponent(workId)}` : ""}`,
              );
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <div hidden={view !== "batches"}>
        <DashboardPage embedded service={batches} />
      </div>
      {filesVisited && (
        <div hidden={view !== "files"}>
          <OrganizationPage
            mode="conversions"
            independent
            service={organization}
            initialFilters={initialFilters}
            initialCursor={initialCursor}
            onContextChange={updateContext}
          />
        </div>
      )}
    </>
  );
}
