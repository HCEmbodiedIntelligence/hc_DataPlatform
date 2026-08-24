import type { ViewerWindowPayload } from "./types";

export interface ViewerWindowCanvasRenderInput {
  readonly canvas: HTMLCanvasElement;
  readonly context: CanvasRenderingContext2D;
  readonly payload: ViewerWindowPayload | null;
  readonly currentNs: string;
  readonly startNs: string;
  readonly endNs: string;
}

function timelinePosition(
  valueNs: string,
  startNs: string,
  endNs: string,
): number {
  const start = BigInt(startNs);
  const end = BigInt(endNs);
  if (end <= start) return 0;
  const value = BigInt(valueNs);
  if (value <= start) return 0;
  if (value >= end) return 1;
  return Number(((value - start) * 10_000n) / (end - start)) / 10_000;
}

function setCanvasSize(
  canvas: HTMLCanvasElement,
  context: CanvasRenderingContext2D,
): { readonly width: number; readonly height: number } {
  const cssWidth = canvas.clientWidth || 320;
  const cssHeight = canvas.clientHeight || 180;
  const ratio = Math.max(1, Math.min(globalThis.devicePixelRatio || 1, 2));
  const width = Math.round(cssWidth * ratio);
  const height = Math.round(cssHeight * ratio);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width: cssWidth, height: cssHeight };
}

function drawGrid(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
): void {
  context.strokeStyle = "rgba(147, 164, 191, 0.28)";
  context.lineWidth = 1;
  for (let column = 1; column < 5; column += 1) {
    const x = (width * column) / 5;
    context.beginPath();
    context.moveTo(x, 0);
    context.lineTo(x, height);
    context.stroke();
  }
  for (let row = 1; row < 4; row += 1) {
    const y = (height * row) / 4;
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  }
}

function drawValues(
  context: CanvasRenderingContext2D,
  values: readonly (readonly number[])[],
  width: number,
  height: number,
): void {
  const samples = values.flatMap((value) => {
    const sample = value[0];
    return typeof sample === "number" && Number.isFinite(sample)
      ? [sample]
      : [];
  });
  if (!samples.length) return;
  const low = Math.min(...samples);
  const high = Math.max(...samples);
  const span = high - low || 1;
  context.strokeStyle = "#2d70d7";
  context.lineWidth = 1.75;
  context.beginPath();
  samples.forEach((value, index) => {
    const x =
      samples.length === 1 ? width / 2 : (index * width) / (samples.length - 1);
    const y = height - ((value - low) / span) * height;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
}

function closestPointFrame(payload: ViewerWindowPayload, currentNs: string) {
  const frames = payload.pointFrames;
  if (!frames?.length) return payload.points ?? null;
  const current = BigInt(currentNs);
  return frames.reduce((nearest, candidate) => {
    const currentDistance =
      BigInt(nearest.timestampNs) > current
        ? BigInt(nearest.timestampNs) - current
        : current - BigInt(nearest.timestampNs);
    const candidateDistance =
      BigInt(candidate.timestampNs) > current
        ? BigInt(candidate.timestampNs) - current
        : current - BigInt(candidate.timestampNs);
    return candidateDistance < currentDistance ? candidate : nearest;
  }).points;
}

function drawPointCloud(
  context: CanvasRenderingContext2D,
  points: Float32Array,
  width: number,
  height: number,
): void {
  if (points.length < 3) return;
  let minX = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (let index = 0; index < points.length; index += 3) {
    const x = points[index] ?? 0;
    const y = points[index + 1] ?? 0;
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  }
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const count = points.length / 3;
  const stride = Math.max(1, Math.ceil(count / 20_000));
  context.fillStyle = "#2d70d7";
  for (let index = 0; index < points.length; index += 3 * stride) {
    const pointX = points[index] ?? 0;
    const pointY = points[index + 1] ?? 0;
    const x = ((pointX - minX) / spanX) * width;
    const y = height - ((pointY - minY) / spanY) * height;
    context.fillRect(x, y, 1.5, 1.5);
  }
}

function drawEvents(
  context: CanvasRenderingContext2D,
  payload: ViewerWindowPayload,
  startNs: string,
  endNs: string,
  width: number,
  height: number,
): void {
  for (const event of payload.events ?? []) {
    const x = timelinePosition(event.timestampNs, startNs, endNs) * width;
    context.strokeStyle = "#9b5de5";
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(x, height * 0.2);
    context.lineTo(x, height * 0.8);
    context.stroke();
  }
}

/** Draw only payload values sourced from the immutable P06 binding. */
export function drawViewerWindowCanvas(
  input: ViewerWindowCanvasRenderInput,
): void {
  const { canvas, context, payload, currentNs, startNs, endNs } = input;
  const { width, height } = setCanvasSize(canvas, context);
  context.clearRect(0, 0, width, height);
  drawGrid(context, width, height);
  if (payload?.pointFrames?.length || payload?.points) {
    const points = closestPointFrame(payload, currentNs);
    if (points) drawPointCloud(context, points, width, height);
  } else if (payload?.values?.length) {
    drawValues(context, payload.values, width, height);
  } else if (payload?.events?.length) {
    drawEvents(context, payload, startNs, endNs, width, height);
  }
  const cursor = timelinePosition(currentNs, startNs, endNs) * width;
  context.strokeStyle = "#ff6b35";
  context.lineWidth = 1.25;
  context.beginPath();
  context.moveTo(cursor, 0);
  context.lineTo(cursor, height);
  context.stroke();
}

export function viewerWindowSummary(payload: ViewerWindowPayload): string {
  if (payload.pointFrames?.length || payload.points)
    return `已加载 ${payload.pointFrames?.length ?? 1} 帧真实点云数据；画面随共享时间轴切换最近样本。`;
  if (payload.events?.length)
    return `已加载 ${payload.events.length} 条真实事件；竖线对应共享时间轴上的事件时刻。`;
  if (payload.values?.length)
    return `已加载 ${payload.values.length} 个真实数值样本；折线使用每个样本的首个数值维度。`;
  return "已加载固定采集数据。";
}
