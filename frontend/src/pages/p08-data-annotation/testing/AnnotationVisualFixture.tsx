import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import { createRoot, type Root } from "react-dom/client";
import { createDomainError } from "../../../shared/api/domain-error";
import type {
  StreamDescriptor,
  ViewerPanelRenderContext,
} from "../../../features/viewer";
import { AnnotationWorkbenchView } from "../AnnotationWorkbenchView";
import type { RuntimeAnnotationTag } from "../runtime-annotation-adapter";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./annotation-fixture";
import styles from "./AnnotationVisualFixture.module.css";

export type AnnotationVisualScenario =
  | "reference"
  | "empty-cameras"
  | "conflict";

export interface AnnotationVisualFixtureOptions {
  readonly mode?: "annotation" | "tag-review";
  readonly cameraCount?: number;
  readonly scenario?: AnnotationVisualScenario;
}

const visualJointAngleStream: StreamDescriptor = {
  id: "visual-joint-angles",
  canonicalPath: "/robot/joint_states",
  displayName: "关节角变化",
  modality: "joint_state",
  schema: {
    id: "sensor_msgs/msg/JointState",
    version: "visual-fixture",
    unit: "rad",
  },
  rateHz: 30,
  startNs: "0",
  endNs: "892900000000",
  availability: "ready",
  accessibleSummary: "视觉测试固定关节角窗口。",
  windowSource: {
    async loadWindow(window) {
      const startStep = Number(BigInt(window.startNs) / 33_333_333n);
      const endStep = Math.max(
        startStep + 2,
        Number(BigInt(window.endNs) / 33_333_333n),
      );
      const stride = Math.max(1, Math.ceil((endStep - startStep) / 180));
      const steps = Array.from(
        { length: Math.ceil((endStep - startStep) / stride) },
        (_, index) => startStep + index * stride,
      );
      return {
        generation: 0,
        timestampsNs: steps.map((step) => `${BigInt(step) * 33_333_333n}`),
        values: steps.map((step) =>
          Array.from({ length: 7 }, (_, joint) => {
            const phase = step / 22 + joint * 0.72;
            return Math.sin(phase) * (0.72 + joint * 0.06) + joint * 0.08;
          }),
        ),
        series: Array.from({ length: 7 }, (_, index) => ({
          id: `joint-${index + 1}`,
          displayName: `J${index + 1}`,
          unit: "rad",
        })),
      };
    },
  },
};

function CameraClock({
  context,
}: {
  readonly context: ViewerPanelRenderContext;
}): JSX.Element {
  const ref = useRef<HTMLTimeElement>(null);
  useEffect(
    () =>
      context.clock.subscribe((value) => {
        if (!ref.current) return;
        const seconds = Number(BigInt(value) / 1_000_000n) / 1000;
        ref.current.textContent = `${seconds.toFixed(2)}s`;
        ref.current.dateTime = value;
      }),
    [context.clock],
  );
  return (
    <time className={styles.clock} ref={ref}>
      0.00s
    </time>
  );
}

function RobotCameraScene({ index }: { readonly index: number }): JSX.Element {
  const shift = (index % 4) * 24;
  return (
    <svg
      aria-label="确定性机器人相机视觉测试画面"
      role="img"
      viewBox="0 0 640 360"
    >
      <defs>
        <linearGradient
          id={`fixture-floor-${index}`}
          x1="0"
          x2="0"
          y1="0"
          y2="1"
        >
          <stop offset="0" stopColor={index === 3 ? "#123b4f" : "#46505e"} />
          <stop offset="1" stopColor="#161b24" />
        </linearGradient>
      </defs>
      <rect width="640" height="360" fill={`url(#fixture-floor-${index})`} />
      <g opacity=".24" stroke="#d6e0ea" strokeWidth="1">
        {Array.from({ length: 9 }, (_, line) => (
          <path d={`M0 ${line * 45}H640`} key={`h-${line}`} />
        ))}
        {Array.from({ length: 15 }, (_, line) => (
          <path d={`M${line * 46} 0V360`} key={`v-${line}`} />
        ))}
      </g>
      <g
        transform={`translate(${290 + shift} 242) rotate(${index % 2 ? -12 : 8})`}
      >
        <ellipse cx="0" cy="62" fill="#0a0d12" opacity=".55" rx="122" ry="22" />
        <rect x="-60" y="26" width="120" height="58" rx="12" fill="#d6dbe2" />
        <circle cy="24" r="39" fill="#919ba9" />
        <g transform="rotate(-34)">
          <rect
            x="-28"
            y="-86"
            width="56"
            height="118"
            rx="26"
            fill="#dce1e7"
          />
          <circle cy="-87" r="31" fill="#87919f" />
          <g transform="translate(0 -87) rotate(57)">
            <rect
              x="-24"
              y="-108"
              width="48"
              height="112"
              rx="22"
              fill="#cdd4dd"
            />
            <circle cy="-108" r="27" fill="#7e8998" />
            <g transform="translate(0 -108) rotate(-18)">
              <rect
                x="-20"
                y="-72"
                width="40"
                height="78"
                rx="18"
                fill="#d6dbe2"
              />
              <rect
                x="-29"
                y="-106"
                width="58"
                height="42"
                rx="10"
                fill="#202733"
              />
              <path
                d="M-18-106L-31-139M18-106L31-139"
                fill="none"
                stroke="#b8c1cb"
                strokeWidth="9"
              />
            </g>
          </g>
        </g>
      </g>
      <rect
        x="116"
        y="86"
        width="92"
        height="62"
        rx="5"
        fill="none"
        stroke="#8b96ff"
        strokeWidth="3"
      />
      <path d="M208 116H270V156" fill="none" stroke="#8b96ff" strokeWidth="2" />
      <text x="276" y="160" fill="#c7ceff" fontFamily="monospace" fontSize="13">
        TAG SYNC
      </text>
    </svg>
  );
}

function renderCamera(context: ViewerPanelRenderContext): JSX.Element {
  if (context.stream.semanticRole === "camera-slot-placeholder")
    return <>{context.defaultPanel}</>;
  const index = Number(context.stream.canonicalPath.match(/(\d+)/u)?.[1] ?? 0);
  return (
    <article
      className={`${styles.cameraPanel} viewer-panel`}
      aria-label={`${context.stream.displayName} 测试画面`}
    >
      <header>
        <h3>{context.stream.displayName}</h3>
        <span>30 Hz · 数据清单</span>
      </header>
      <RobotCameraScene index={index} />
      <CameraClock context={context} />
    </article>
  );
}

function AnnotationVisualFixture(
  props: AnnotationVisualFixtureOptions,
): JSX.Element {
  const mode = props.mode ?? "annotation";
  const scenario = props.scenario ?? "reference";
  const bundle = createVisualAnnotationBundle({
    mode,
    cameraCount: scenario === "empty-cameras" ? 0 : (props.cameraCount ?? 4),
    ...(scenario === "conflict"
      ? { invalid: "missing-required" as const }
      : {}),
  });
  const annotationTags = bundle.draft?.tags ?? [];
  const visualParent = annotationTags[0];
  const visualChild: RuntimeAnnotationTag | null = visualParent
    ? {
        ...visualParent,
        annotation_id: "annotation-visual-child",
        tag_id: "manual-visual-child",
        label: "夹爪闭合",
        parent_annotation_id: visualParent.annotation_id,
        path: [...visualParent.path, "manual-visual-child"],
        start_step: visualParent.start_step + 28,
        end_step: visualParent.end_step - 24,
      }
    : null;
  const initialTags =
    mode === "annotation"
      ? visualChild
        ? [...annotationTags, visualChild]
        : annotationTags
      : (bundle.history.revisions.at(-1)?.tags ?? []);
  const [tags, setTags] =
    useState<readonly RuntimeAnnotationTag[]>(initialTags);
  const externalError =
    scenario === "conflict"
      ? createDomainError({
          code: "VERSION_CONFLICT",
          problemCode: "ANNOTATION_CONCURRENT_UPDATE",
          message: "草稿已由另一会话更新，请刷新后比较修订。",
          fieldErrors: [],
          operationErrors: [],
          blockedReasons: [{ code: "ETAG_STALE", message: "If-Match 已过期" }],
          requestId: "visual-request-409",
          retryable: false,
          httpStatus: 409,
        })
      : undefined;
  return (
    <div className={styles.fixture} data-p08-visual-fixture={mode}>
      <AnnotationWorkbenchView
        bundle={bundle}
        dirty={scenario === "conflict"}
        externalError={externalError}
        mode={mode}
        jointAngleStream={visualJointAngleStream}
        permissions={{
          hasAnnotationDraft: mode === "annotation",
          canCreate: false,
          canEdit: mode === "annotation",
          canSave: mode === "annotation",
          canSubmit: mode === "annotation",
          canReview: mode === "tag-review",
          canRevise: mode === "annotation",
          ...(scenario === "conflict"
            ? { readOnlyReason: "并发 409 后写操作保持关闭，直到显式刷新。" }
            : {}),
        }}
        renderPanel={renderCamera}
        scope={visualAnnotationScope}
        tags={tags}
        onReview={() => Promise.resolve()}
        onSave={() => Promise.resolve()}
        onSubmit={() => Promise.resolve()}
        onTagsChange={setTags}
      />
    </div>
  );
}

const mountedRoots = new WeakMap<HTMLElement, Root>();

export function mountAnnotationVisualFixture(
  host: HTMLElement,
  options: AnnotationVisualFixtureOptions = {},
): void {
  mountedRoots.get(host)?.unmount();
  const root = createRoot(host);
  mountedRoots.set(host, root);
  root.render(<AnnotationVisualFixture {...options} />);
}
