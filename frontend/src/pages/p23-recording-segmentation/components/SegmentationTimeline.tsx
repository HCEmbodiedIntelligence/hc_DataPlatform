import {
  useMemo,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import type { EditableSlice } from "../model";
import { clampBoundary, formatTimecode } from "../model";
import styles from "../styles.module.css";

interface BoundaryDrag {
  readonly pointerId: number;
  readonly episodeId: string;
  readonly edge: "start" | "end";
}

export interface SegmentationTimelineProps {
  readonly durationNs: number;
  readonly currentNs: number;
  readonly markInNs: number | null;
  readonly slices: readonly EditableSlice[];
  readonly selectedId: string | null;
  readonly zoom: number;
  readonly disabled: boolean;
  readonly minimumDurationNs: number;
  readonly onSeek: (valueNs: number) => void;
  readonly onSelect: (episodeId: string) => void;
  readonly onChange: (slices: readonly EditableSlice[]) => void;
}

function percent(valueNs: number, durationNs: number): number {
  return durationNs <= 0 ? 0 : (valueNs / durationNs) * 100;
}

export function SegmentationTimeline({
  durationNs,
  currentNs,
  markInNs,
  slices,
  selectedId,
  zoom,
  disabled,
  minimumDurationNs,
  onSeek,
  onSelect,
  onChange,
}: Readonly<SegmentationTimelineProps>) {
  const [drag, setDrag] = useState<BoundaryDrag | null>(null);
  const ticks = useMemo(
    () => Array.from({ length: 9 }, (_, index) => (durationNs * index) / 8),
    [durationNs],
  );

  const nsAtPointer = (event: ReactPointerEvent<HTMLElement>): number => {
    const canvas = event.currentTarget.closest<HTMLElement>(
      "[data-timeline-canvas]",
    );
    if (!canvas) return currentNs;
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(
      1,
      Math.max(0, (event.clientX - rect.left) / rect.width),
    );
    return Math.round(durationNs * ratio);
  };

  const startDrag = (
    event: ReactPointerEvent<HTMLButtonElement>,
    episodeId: string,
    edge: "start" | "end",
  ) => {
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ pointerId: event.pointerId, episodeId, edge });
  };

  const moveDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    onChange(
      clampBoundary(
        slices,
        drag.episodeId,
        drag.edge,
        nsAtPointer(event),
        durationNs,
        minimumDurationNs,
      ),
    );
  };

  return (
    <section className={styles.timelinePanel} aria-label="Episode 切片时间轴">
      <div className={styles.timelineLegend}>
        <span>
          <i className={styles.legendEpisode} />
          Episode
        </span>
        <span>
          <i className={styles.legendMark} />
          入点
        </span>
        <span>
          <i className={styles.legendPlayhead} />
          播放头
        </span>
        <span className={styles.timelineHint}>拖动切片两端可微调边界</span>
      </div>
      <div className={styles.timelineViewport}>
        <div
          className={styles.timelineCanvas}
          data-timeline-canvas
          style={{ width: `${zoom * 100}%` }}
          onPointerDown={(event) => {
            if (
              (event.target as HTMLElement).closest("[data-timeline-control]")
            )
              return;
            onSeek(nsAtPointer(event));
          }}
        >
          <div className={styles.timelineTicks} aria-hidden="true">
            {ticks.map((tick, index) => (
              <span
                key={index}
                style={{ left: `${percent(tick, durationNs)}%` }}
              >
                {formatTimecode(tick, false)}
              </span>
            ))}
          </div>
          <div className={styles.timelineTrack}>
            {slices.map((slice, index) => (
              <div
                role="button"
                tabIndex={0}
                key={slice.episodeId}
                data-timeline-control
                className={styles.timelineSegment}
                data-selected={slice.episodeId === selectedId || undefined}
                style={{
                  left: `${percent(slice.startNs, durationNs)}%`,
                  width: `${Math.max(0.08, percent(slice.endNs - slice.startNs, durationNs))}%`,
                }}
                onClick={(event) => {
                  event.stopPropagation();
                  onSelect(slice.episodeId);
                  onSeek(slice.startNs);
                }}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  onSelect(slice.episodeId);
                  onSeek(slice.startNs);
                }}
                aria-label={`选择 Episode ${index + 1}，${formatTimecode(slice.startNs)} 至 ${formatTimecode(slice.endNs)}`}
              >
                <span>{index + 1}</span>
                {!disabled ? (
                  <>
                    <button
                      type="button"
                      data-timeline-control
                      className={styles.timelineHandle}
                      data-edge="start"
                      aria-label={`调整 Episode ${index + 1} 开始时间`}
                      onClick={(event) => event.stopPropagation()}
                      onPointerDown={(event) =>
                        startDrag(event, slice.episodeId, "start")
                      }
                      onPointerMove={moveDrag}
                      onPointerUp={() => setDrag(null)}
                      onPointerCancel={() => setDrag(null)}
                    />
                    <button
                      type="button"
                      data-timeline-control
                      className={styles.timelineHandle}
                      data-edge="end"
                      aria-label={`调整 Episode ${index + 1} 结束时间`}
                      onClick={(event) => event.stopPropagation()}
                      onPointerDown={(event) =>
                        startDrag(event, slice.episodeId, "end")
                      }
                      onPointerMove={moveDrag}
                      onPointerUp={() => setDrag(null)}
                      onPointerCancel={() => setDrag(null)}
                    />
                  </>
                ) : null}
              </div>
            ))}
            {markInNs !== null ? (
              <span
                className={styles.markIn}
                style={{ left: `${percent(markInNs, durationNs)}%` }}
                aria-label={`入点 ${formatTimecode(markInNs)}`}
              />
            ) : null}
            <span
              className={styles.playhead}
              style={{ left: `${percent(currentNs, durationNs)}%` }}
              aria-label={`播放头 ${formatTimecode(currentNs)}`}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
