/** Media types describe artifacts; format identifiers never imply ZIP bytes. */
export function exportDownloadName(stem: string, mediaType: string): string {
  const extensions: Record<string, string> = {
    "application/zip": ".zip",
    "application/json": ".json",
    "application/x-ndjson": ".jsonl",
    "text/csv": ".csv",
  };
  const safe = stem.replace(/[\\/:*?"<>|]/gu, "_").replace(/\p{Cc}/gu, "_").slice(0, 180);
  return `${safe || "data-export"}${extensions[mediaType.split(";")[0]!.trim()] ?? ""}`;
}
