export function exportArchiveName(mode: string, datasets: readonly string[], now = new Date()): string {
  const labels = [...new Set(datasets.map(id => id.replace(/[^A-Za-z0-9_-]+/gu, "-").slice(0, 48)))].sort();
  const selected = labels.slice(0, 3).join("+") + (labels.length > 3 ? `+${labels.length - 3}more` : "");
  const format = mode === "independent" ? "lerobot" : ["rgb", "rgbd"].includes(mode) ? `lerobot_${mode}` : mode;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(now);
  const clock = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${format}_${selected}_${clock.year}${clock.month}${clock.day}_${clock.hour}${clock.minute}.zip`;
}
