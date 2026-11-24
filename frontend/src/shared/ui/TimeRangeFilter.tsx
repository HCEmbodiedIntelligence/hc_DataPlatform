import { Button, DatePicker } from "antd";
import dayjs, { type Dayjs } from "dayjs";
import "dayjs/locale/zh-cn";
import { useMemo, useState } from "react";
import type { TimeRange } from "../lib/time-range";

const hours = Array.from({ length: 24 }, (_, hour) => hour);
const minutes = Array.from({ length: 60 }, (_, minute) => minute);
const dayMs = 86_400_000;

export function TimeRangeFilter({
  range,
  onChange,
  label,
  required,
  maxDays,
}: {
  range?: TimeRange;
  onChange: (range: TimeRange | undefined) => void;
  label: string;
  required?: boolean;
  maxDays?: number;
}) {
  const from = range?.from;
  const to = range?.to;
  const value = useMemo<[Dayjs, Dayjs] | null>(
    () => (from && to ? [dayjs(from), dayjs(to)] : null),
    [from, to],
  );
  const [editingEnd, setEditingEnd] = useState(false);
  const [draftStart, setDraftStart] = useState<Dayjs | null>(null);
  const [error, setError] = useState("");
  const [presetRevision, setPresetRevision] = useState(0);
  const start = draftStart ?? value?.[0];
  const firstEnd = start?.startOf("minute").add(1, "minute");
  const lastEnd =
    start && maxDays ? start.add(maxDays * dayMs, "ms") : undefined;

  function selectPreset(days: number) {
    const end = dayjs().startOf("minute");
    setError("");
    setDraftStart(null);
    setEditingEnd(false);
    // Discard any partially confirmed manual edit when applying a whole range.
    setPresetRevision((revision) => revision + 1);
    onChange({
      from: end.subtract(days * dayMs, "ms").toISOString(),
      to: end.toISOString(),
    });
  }

  return (
    <div>
      <DatePicker.RangePicker
        key={presetRevision}
        aria-label={label}
        placeholder={["开始时间", "结束时间"]}
        allowClear={!required}
        previewValue={false}
        order={false}
        format="YYYY-MM-DD HH:mm"
        minDate={editingEnd ? start : undefined}
        maxDate={editingEnd ? lastEnd : undefined}
        showTime={{
          format: "HH:mm",
          disabledTime: (date, field) => {
            if (field !== "end") return {};
            if (firstEnd && date.isBefore(firstEnd, "day"))
              return { disabledHours: () => hours };
            const min = firstEnd?.isSame(date, "day") ? firstEnd : undefined;
            const max = lastEnd?.isSame(date, "day") ? lastEnd : undefined;
            return {
              disabledHours: () =>
                hours.filter(
                  (h) => (min && h < min.hour()) || (max && h > max.hour()),
                ),
              disabledMinutes: (h) =>
                minutes.filter(
                  (m) =>
                    (min && h === min.hour() && m < min.minute()) ||
                    (max && h === max.hour() && m > max.minute()),
                ),
            };
          },
        }}
        value={value}
        onFocus={(_, info) => setEditingEnd(info.range === "end")}
        onCalendarChange={(dates) => setDraftStart(dates?.[0] ?? null)}
        onOpenChange={(open) => {
          if (open) setError("");
          else setDraftStart(null);
        }}
        panelRender={(panel) => (
          // Keep the active input focused during mouse selection, including month navigation.
          <div onMouseDown={(event) => event.preventDefault()}>{panel}</div>
        )}
        renderExtraFooter={() =>
          [1, 7, 30]
            .filter((days) => !maxDays || days <= maxDays)
            .map((days) => (
              <Button
                key={days}
                type="link"
                size="small"
                onClick={() => selectPreset(days)}
              >
                {days === 1 ? "最近 24 小时" : `最近 ${days} 天`}
              </Button>
            ))
        }
        onChange={(dates) => {
          const [begin, end] = dates ?? [];
          if (begin && end) {
            if (!end.isAfter(begin, "minute")) {
              setError("结束时间须晚于开始时间");
              return;
            }
            if (maxDays && end.diff(begin) > maxDays * dayMs) {
              setError(`每次最多可查看 ${maxDays} 天`);
              return;
            }
          }
          setError("");
          onChange(
            begin && end
              ? { from: begin.toISOString(), to: end.toISOString() }
              : undefined,
          );
        }}
      />
      {error && <small role="alert">{error}</small>}
    </div>
  );
}
