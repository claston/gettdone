import type { SourceRegion } from "@/domain/workspace";

export function PdfSourceHighlights({
  regions,
  transactionId,
}: {
  regions: SourceRegion[];
  transactionId?: string;
}) {
  return regions.map((region, index) => (
    <div
      key={`${region.page}:${index}`}
      className="pdf-source-highlight"
      data-source-region={index}
      data-source-transaction={transactionId}
      role="img"
      aria-label={`Origem do lançamento selecionado · página ${region.page}`}
      style={{
        left: `${region.left * 100}%`,
        top: `${region.top * 100}%`,
        width: `${region.width * 100}%`,
        height: `${region.height * 100}%`,
      }}
    />
  ));
}
