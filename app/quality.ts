type VideoFormat = { id: string; label: string; kind: string };

export function videoHeight(format: VideoFormat): number {
  if (format.kind === "音频" || format.kind === "图片") return 0;
  const label = format.label.trim().toUpperCase();
  if (label === "4K") return 2160;
  if (label === "8K") return 4320;
  // A frame-rate or codec suffix does not change the source resolution.
  // Anchor at the beginning so a bitrate such as "1200 kbps" is not a height.
  return Number(/^(\d{3,4})P(?=$|[\s·+(/])/.exec(label)?.[1] ?? 0);
}

export function maxVideoHeight(formats: readonly VideoFormat[]): number {
  return Math.max(0, ...formats.map(videoHeight));
}

export function preferredFormatId(platformName: string, formats: readonly VideoFormat[]): string {
  // Prefer the highest video rendition the resolver actually returned. Some
  // extractors put a low-bandwidth "fast" option first; images and audio must
  // never win just because their labels happen to contain a number.
  void platformName;
  const firstVideo = formats.find((format) => format.kind !== "音频" && format.kind !== "图片");
  return formats.reduce<{ id: string; height: number; isFast: boolean }>(
    (best, format) => {
      if (format.kind === "音频" || format.kind === "图片") return best;
      const height = videoHeight(format);
      const isFast = format.kind === "快速";
      // For unnumbered choices (TED bitrate labels, adaptive HLS), the
      // resolver already orders the best first.  Only replace a fast option
      // at a genuinely equal numeric resolution.
      return height > best.height || (height > 0 && height === best.height && best.isFast && !isFast)
        ? { id: format.id, height, isFast }
        : best;
    },
    { id: firstVideo?.id ?? formats[0]?.id ?? "", height: 0, isFast: firstVideo?.kind === "快速" },
  ).id;
}

export function sortFormatsForDisplay<T extends VideoFormat>(formats: readonly T[]): T[] {
  return formats.map((format, index) => ({ format, index })).sort((a, b) => {
    const group = (item: VideoFormat) => item.kind === "图片" ? 2 : item.kind === "音频" ? 1 : 0;
    const groupDifference = group(a.format) - group(b.format);
    if (groupDifference) return groupDifference;
    if (group(a.format) === 0) {
      const heightDifference = videoHeight(b.format) - videoHeight(a.format);
      if (heightDifference) return heightDifference;
      if (a.format.kind === "快速" && b.format.kind !== "快速") return 1;
      if (b.format.kind === "快速" && a.format.kind !== "快速") return -1;
    }
    return a.index - b.index;
  }).map(({ format }) => format);
}
