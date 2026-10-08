import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Typography } from "antd";
import type { Scope } from "../../shared/scope/model";
import type { ReviewDataRef } from "../../features/datasets/episodes/data-reviews";
import {
  imagePixel,
  loadDepthPixel,
  loadDepthPreview,
  type DepthFrameTarget,
} from "./depth-pixels";

export type DepthCamera = {
  feature: string;
  label: string;
  start: number;
  end: number;
};

function FrameInspector({ target }: { target: DepthFrameTarget }) {
  const [url, setUrl] = useState<string>();
  const [x, setX] = useState(0);
  const [y, setY] = useState(0);
  const [point, setPoint] = useState<{ x: number; y: number }>();
  const preview = useQuery({
    queryKey: ["depth-pixel-preview", target],
    queryFn: ({ signal }) => loadDepthPreview(target, signal),
    gcTime: 0,
  });
  useEffect(() => {
    if (!preview.data) return;
    const source = URL.createObjectURL(preview.data);
    setUrl(source);
    return () => URL.revokeObjectURL(source);
  }, [preview.data]);
  const pixel = useQuery({
    queryKey: ["depth-pixel", target, point],
    queryFn: ({ signal }) => loadDepthPixel(target, point!.x, point!.y, signal),
    enabled: point !== undefined,
    gcTime: 0,
  });
  const result = pixel.data;
  return (
    <>
      <Typography.Paragraph>
        取值帧 {target.frame} · 点击图像或输入原深度坐标（从 0 开始）。
      </Typography.Paragraph>
      {preview.isPending && (
        <Typography.Paragraph>加载 RealSense 官方着色帧…</Typography.Paragraph>
      )}
      {preview.error && (
        <Alert
          type="error"
          title="深度帧加载失败"
          description={preview.error.message}
        />
      )}
      {url && !preview.error && (
        <img
          src={url}
          alt={`RealSense 深度取值帧 ${target.frame}`}
          style={{ maxWidth: "100%", height: "auto", cursor: "crosshair" }}
          onClick={(event) => {
            const image = event.currentTarget;
            const selected = imagePixel(
              event.clientX,
              event.clientY,
              image.getBoundingClientRect(),
              image.naturalWidth,
              image.naturalHeight,
            );
            if (selected) {
              setX(selected.x);
              setY(selected.y);
              setPoint(selected);
            }
          }}
        />
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <label>
          像素 x{" "}
          <input
            aria-label="像素 x"
            type="number"
            min={0}
            step={1}
            value={Number.isNaN(x) ? "" : x}
            onChange={(e) => setX(e.target.valueAsNumber)}
            style={{ width: 80 }}
          />
        </label>
        <label>
          像素 y{" "}
          <input
            aria-label="像素 y"
            type="number"
            min={0}
            step={1}
            value={Number.isNaN(y) ? "" : y}
            onChange={(e) => setY(e.target.valueAsNumber)}
            style={{ width: 80 }}
          />
        </label>
        <Button
          disabled={
            ![x, y].every((value) => Number.isSafeInteger(value) && value >= 0)
          }
          onClick={() => setPoint({ x, y })}
        >
          读取物理深度
        </Button>
      </div>
      {pixel.isFetching && (
        <Typography.Paragraph>读取像素…</Typography.Paragraph>
      )}
      {pixel.error && (
        <Alert
          type="error"
          title="像素读取失败"
          description={pixel.error.message}
        />
      )}
      {result && !pixel.error && (
        <div aria-live="polite">
          <Typography.Paragraph strong>
            像素 ({result.x}, {result.y})：
            {result.valid
              ? `${result.value!.toFixed(6)} m`
              : "无效，无物理测量值"}
          </Typography.Paragraph>
          {(result.saturated_low || result.saturated_high) && (
            <Alert
              type="warning"
              title={
                result.saturated_low
                  ? "低于工作距离，显示下限裁剪值"
                  : "超过工作距离，显示上限裁剪值"
              }
            />
          )}
          {result.source.missing === true && (
            <Alert type="warning" title="源帧缺失" />
          )}
          {result.source.repeated === true && (
            <Typography.Paragraph>重复使用源帧</Typography.Paragraph>
          )}
          <Typography.Paragraph>
            12 位{result.profile.use_log ? "对数" : "线性"}量化反解；工作距离{" "}
            {result.profile.depth_min}–{result.profile.depth_max}{" "}
            m。数值的小数位不代表传感器精度。
          </Typography.Paragraph>
          <details>
            <summary>源帧与量化配置</summary>
            <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {JSON.stringify(
                { profile: result.profile, source: result.source },
                null,
                2,
              )}
            </pre>
          </details>
        </div>
      )}
    </>
  );
}

export function DepthPixelPanel({
  scope,
  data,
  cameras,
  canRead,
}: {
  scope: Scope;
  data: ReviewDataRef;
  cameras: DepthCamera[];
  canRead: boolean;
}) {
  const [feature, setFeature] = useState(cameras[0]?.feature ?? "");
  const camera = cameras.find((item) => item.feature === feature);
  const [frame, setFrame] = useState(camera?.start ?? 0);
  const [selected, setSelected] = useState<{
    feature: string;
    frame: number;
  }>();
  if (!cameras.length) return null;
  return (
    <Card size="small" title="深度像素取值">
      {!canRead ? (
        <Alert type="warning" title="缺少数据版本读取权限" />
      ) : (
        <>
          <Typography.Paragraph>
            颜色由 RealSense 官方 colorizer
            按帧动态分布，距离以取值为准。取值使用原深度坐标；此处未进行 RGB
            空间注册，不能将 RGB 像素坐标直接对应到深度。
          </Typography.Paragraph>
          <label>
            深度相机{" "}
            <select
              aria-label="深度相机"
              value={feature}
              onChange={(e) => {
                setFeature(e.target.value);
                setFrame(
                  cameras.find((item) => item.feature === e.target.value)
                    ?.start ?? 0,
                );
                setSelected(undefined);
              }}
            >
              {cameras.map((item) => (
                <option key={item.feature} value={item.feature}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            取值帧{" "}
            <input
              aria-label="取值帧"
              type="number"
              min={camera?.start}
              max={(camera?.end ?? 1) - 1}
              step={1}
              value={Number.isNaN(frame) ? "" : frame}
              onChange={(e) => setFrame(e.target.valueAsNumber)}
              style={{ width: 90 }}
            />
          </label>
          <Button
            disabled={
              !camera ||
              !Number.isSafeInteger(frame) ||
              frame < camera.start ||
              frame >= camera.end
            }
            onClick={() => setSelected({ feature, frame })}
          >
            查看取值帧
          </Button>
          {selected && (
            <FrameInspector
              key={`${selected.feature}:${selected.frame}`}
              target={{ scope, data, ...selected }}
            />
          )}
        </>
      )}
    </Card>
  );
}
