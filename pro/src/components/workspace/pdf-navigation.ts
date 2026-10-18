export interface PageSize {
  width: number;
  height: number;
}
export interface PageBox extends PageSize {
  top: number;
}
export function pageLayout(sizes: PageSize[], width: number): PageBox[] {
  let top = 0;
  return sizes.map((size) => {
    const height = (width * size.height) / size.width;
    const box = { top, width, height };
    top += height + 16;
    return box;
  });
}
export function activePageAt(
  pages: PageBox[],
  scrollTop: number,
  viewportHeight: number,
) {
  const last = pages.at(-1);
  if (
    last &&
    scrollTop > 0 &&
    scrollTop + viewportHeight >= last.top + last.height - 1
  )
    return pages.length;
  // A fixed reading line avoids oscillation when adjacent pages are both visible.
  const line = scrollTop + Math.min(120, viewportHeight * 0.2);
  let index = 0;
  while (index + 1 < pages.length && pages[index + 1].top <= line) index++;
  return index + 1;
}
export function readingAnchor(pages: PageBox[], top: number) {
  const page = activePageAt(pages, top, 0);
  const box = pages[page - 1];
  return { page, fraction: box ? (top - box.top) / box.height : 0 };
}
export function scrollForAnchor(
  pages: PageBox[],
  anchor: { page: number; fraction: number },
) {
  const box = pages[anchor.page - 1];
  return box ? box.top + box.height * anchor.fraction : 0;
}
