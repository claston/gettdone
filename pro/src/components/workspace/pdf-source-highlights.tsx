import type { SourceRegion } from "@/domain/workspace";

export function PdfSourceHighlights({
  regions,
  transactionId,
  manualRegions = [],
}: {
  regions: SourceRegion[];
  transactionId?: string;
  manualRegions?: readonly SourceRegion[];
}) {
  // A reading ruler spans the page, while the original evidence boxes retain
  // their exact horizontal bounds. Merge fragments on the same physical line.
  const bands: SourceRegion[] = [];
  for (const region of [...regions].sort(
    (a, b) => a.page - b.page || a.top - b.top,
  )) {
    const previous = bands.at(-1);
    if (
      previous &&
      previous.page === region.page &&
      region.top <= previous.top + previous.height
    ) {
      if (region.top + region.height > previous.top + previous.height)
        bands[bands.length - 1] = {
          ...previous,
          height: region.top + region.height - previous.top,
        };
    } else bands.push({ ...region });
  }
  return (
    <>
      {bands.map((region, index) => (
        <div
          key={`marker:${region.page}:${index}`}
          className="pdf-reading-marker"
          data-pdf-reading-marker={index}
          data-source-transaction={transactionId}
          aria-hidden="true"
          style={{
            left: "0%",
            width: "100%",
            top: `${region.top * 100}%`,
            height: `${region.height * 100}%`,
          }}
        />
      ))}
      {regions.map((region, index) => (
        <div
          key={`${region.page}:${index}`}
          className={`pdf-source-highlight ${manualRegions.some((manual) => JSON.stringify(manual) === JSON.stringify(region)) ? "is-manual" : ""}`}
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
      ))}
    </>
  );
}
