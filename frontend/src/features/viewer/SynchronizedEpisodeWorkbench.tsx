import {
  useCallback,
  useId,
  useState,
  type HTMLAttributes,
  type ReactNode,
} from "react";
import { DataVisualizationWorkbench } from "./DataVisualizationWorkbench";
import {
  ViewerJointAngleCurvePanel,
  ViewerRobotPosePanel,
} from "./EpisodeWorkbenchCore";
import type {
  DataVisualizationWorkbenchAdapter,
  DataVisualizationWorkbenchSlots,
} from "./workbench-contract";
import type { DomainError, StreamDescriptor } from "./types";
import { resolveViewerComposition } from "./ViewerCompositionResolver";
import styles from "./SynchronizedEpisodeWorkbench.module.css";

/** The same media, pose, curves and timeline surface for viewing and annotation. */
export function SynchronizedEpisodeWorkbench({
  adapter,
  slots,
  jointAngleStream,
  numericStreams = [],
  jointAngleUnavailableReason,
  jointAngleFooter,
  synchronized = true,
  className,
  ...frameProps
}: Omit<HTMLAttributes<HTMLDivElement>, "children"> & {
  readonly adapter: DataVisualizationWorkbenchAdapter;
  readonly slots?: DataVisualizationWorkbenchSlots;
  readonly jointAngleStream: StreamDescriptor | null;
  readonly numericStreams?: readonly StreamDescriptor[];
  readonly jointAngleUnavailableReason?: string;
  readonly jointAngleFooter?: ReactNode;
  readonly synchronized?: boolean;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const numericIds = new Set(
    resolveViewerComposition(numericStreams)
      .panels.filter((panel) => panel.kind === "time-series")
      .flatMap((panel) => panel.streamIds),
  );
  const available = [
    ...(jointAngleStream ? [jointAngleStream] : []),
    ...numericStreams.filter((stream) => numericIds.has(stream.id)),
  ].filter(
    (stream, index, all) =>
      stream.windowSource &&
      all.findIndex((other) => other.id === stream.id) === index,
  );
  const selected =
    available.find((stream) => stream.id === selectedId) ??
    jointAngleStream ??
    available[0] ??
    null;
  const onResourceError = adapter.onResourceError;
  const onCurveError = useCallback(
    (error: DomainError) => onResourceError?.(error, "curve"),
    [onResourceError],
  );
  return (
    <div
      {...frameProps}
      className={[styles.workbenchFrame, className].filter(Boolean).join(" ")}
      data-episode-workbench="true"
      data-workspace-layout={synchronized ? "synchronized" : undefined}
    >
      <DataVisualizationWorkbench
        showNavigation={false}
        adapter={
          synchronized
            ? {
                ...adapter,
                robotScene: undefined,
                robotSceneUnavailableReason: undefined,
              }
            : adapter
        }
        slots={{
          inspector: () => (
            <div
              className={styles.robotPoseInspector}
              aria-label="机器人姿态同步视图"
              title="拖动旋转；Ctrl / ⌘ + 滚轮缩放模型"
              onWheelCapture={(event) => {
                if (!event.ctrlKey && !event.metaKey) event.stopPropagation();
              }}
            >
              <ViewerRobotPosePanel
                scene={adapter.robotScene}
                unavailableReason={adapter.robotSceneUnavailableReason}
              />
            </div>
          ),
          actionDock: () => (
            <div className={styles.jointAngleDock}>
              {available.length > 1 ? (
                <label className={styles.numericStreamSelector}>
                  数值通道
                  <select
                    aria-label="数值通道"
                    value={selected?.id ?? ""}
                    onChange={(event) => setSelectedId(event.target.value)}
                  >
                    {available.map((stream) => (
                      <option key={stream.id} value={stream.id}>
                        {stream.displayName}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <ViewerJointAngleCurvePanel
                key={`${adapter.id}:${selected?.id ?? "missing"}`}
                clock={adapter.clock}
                stream={selected}
                unavailableReason={
                  selected === jointAngleStream
                    ? jointAngleUnavailableReason
                    : undefined
                }
                onResourceError={onCurveError}
              />
              {jointAngleFooter}
            </div>
          ),
          ...slots,
        }}
      />
    </div>
  );
}

export function CameraViewToolbar({
  view,
  cameras,
  visibleStreams,
  onViewChange,
  children,
}: {
  readonly view: string;
  readonly cameras: readonly { readonly id: string; readonly label: string }[];
  readonly visibleStreams: readonly StreamDescriptor[];
  readonly onViewChange: (view: string) => void;
  readonly children?: ReactNode;
}) {
  const id = useId();
  const connectedCount = visibleStreams.filter(
    (stream) => stream.semanticRole !== "camera-slot-placeholder",
  ).length;
  return (
    <div className={styles.cameraToolbar} data-camera-toolbar="true">
      <div className={styles.cameraToolbarSummary}>
        <span>
          {!cameras.length
            ? "无相机数据 · 使用关节曲线与时间切片"
            : view === "__quad__"
              ? `四路视频 · 已接入 ${connectedCount} / 4 路`
              : `视频视图 · 当前 ${connectedCount} / ${cameras.length} 路`}
        </span>
        <small>保持原始画面比例；视频、机器人姿态和信号共用时间轴。</small>
      </div>
      <div className={styles.cameraToolbarTools}>
        {cameras.length ? (
          <label htmlFor={id}>
            <span>显示视频</span>
            <select
              id={id}
              aria-label="显示视频"
              name="camera-view-select"
              value={view}
              onChange={(event) => onViewChange(event.target.value)}
            >
              <option value="__quad__">四路视频</option>
              <option value="__all__">全部视频（{cameras.length}）</option>
              {cameras.map((camera) => (
                <option key={camera.id} value={camera.id}>
                  {camera.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {children}
      </div>
    </div>
  );
}

export function selectCameraStreams(
  cameras: readonly StreamDescriptor[],
  view: string,
  range: { readonly startNs: string; readonly endNs: string },
): readonly StreamDescriptor[] {
  if (!cameras.length || view === "__all__") return cameras;
  if (view !== "__quad__")
    return cameras.filter((camera) => camera.id === view);
  const visible = cameras.slice(0, 4);
  return [
    ...visible,
    ...Array.from(
      { length: 4 - visible.length },
      (_, index): StreamDescriptor => {
        const slot = visible.length + index + 1;
        return {
          id: `camera-slot-${slot}`,
          canonicalPath: `/camera-slots/${slot}`,
          displayName: `摄像头 ${slot}`,
          modality: "rgb",
          semanticRole: "camera-slot-placeholder",
          schema: { id: "camera-slot-placeholder", version: "1" },
          ...range,
          availability: "missing",
          accessibleSummary: `第 ${slot} 个视频槽位尚未接入摄像头。真实视频会按数据清单顺序从第一格开始显示。`,
        };
      },
    ),
  ];
}
