import { useEffect, useRef, useState } from 'react';
import type { JSX } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { CameraOff } from 'lucide-react';
import { createPlaybackClock } from '../PlaybackClock';
import { RawDiagnosticWorkbench } from '../RawDiagnosticWorkbench';
import type { ViewerPanelRenderContext, ViewerTimelineTrack } from '../EpisodeWorkbenchCore';
import type { RuntimeManifestDiscoveryProjection } from '../raw-diagnostic-adapter';
import type { StreamDescriptor } from '../types';
import styles from './RawDiagnosticVisualFixture.module.css';

export type RawDiagnosticVisualScenario = 'reference' | 'missing-slow' | 'empty';

export interface RawDiagnosticVisualFixtureOptions {
  readonly cameraCount?: number;
  readonly scenario?: RawDiagnosticVisualScenario;
}

const cameraNames = ['主臂相机', '右腕相机', '左腕相机', '深度流'];

function cameraName(index: number): string {
  return cameraNames[index] ?? `扩展相机 ${index + 1}`;
}

function CameraClockBadge({ clock }: { readonly clock: ViewerPanelRenderContext['clock'] }): JSX.Element {
  const ref = useRef<HTMLTimeElement>(null);
  useEffect(() => clock.subscribe((ns) => {
    if (!ref.current) return;
    ref.current.dateTime = ns;
    ref.current.textContent = `${(Number(BigInt(ns) / 1_000_000n) / 1000).toFixed(2)}s`;
  }), [clock]);
  return <time className={styles.cameraTime} ref={ref} />;
}

function RobotTelemetryScene({ index }: { readonly index: number }): JSX.Element {
  const offset = (index % 3) * 34;
  return (
    <svg
      aria-label="确定性机器人相机测试画面"
      className={styles.robotScene}
      role="img"
      viewBox="0 0 640 360"
    >
      <defs>
        <linearGradient id={`floor-${index}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={index === 3 ? '#29303d' : '#3e4654'} />
          <stop offset="1" stopColor="#171b24" />
        </linearGradient>
      </defs>
      <rect width="640" height="360" fill={`url(#floor-${index})`} />
      <g className={styles.gridLines}>
        {Array.from({ length: 9 }, (_, line) => <path d={`M0 ${line * 45} H640`} key={`h-${line}`} />)}
        {Array.from({ length: 15 }, (_, line) => <path d={`M${line * 46} 0 V360`} key={`v-${line}`} />)}
      </g>
      <g transform={`translate(${255 + offset} 224) rotate(${index % 2 ? -13 : 8})`}>
        <ellipse cx="0" cy="72" fill="#0c1017" opacity=".5" rx="126" ry="23" />
        <rect fill="#cbd1da" height="50" rx="11" width="120" x="-60" y="38" />
        <circle cx="0" cy="34" fill="#929baa" r="39" />
        <g transform="rotate(-34)">
          <rect fill="#d8dde4" height="118" rx="28" width="56" x="-28" y="-76" />
          <circle cy="-78" fill="#7e8998" r="31" />
          <g transform="translate(0 -78) rotate(54)">
            <rect fill="#d5dbe3" height="112" rx="24" width="48" x="-24" y="-101" />
            <circle cy="-102" fill="#818c9a" r="27" />
            <g transform="translate(0 -102) rotate(-22)">
              <rect fill="#c5ccd6" height="82" rx="19" width="40" x="-20" y="-72" />
              <rect fill="#202631" height="46" rx="11" width="56" x="-28" y="-109" />
              <path d="M-18 -109 L-30 -139 M18 -109 L30 -139" fill="none" stroke="#b7c0ca" strokeWidth="10" />
            </g>
          </g>
        </g>
      </g>
      <circle cx={112 + offset} cy="88" fill="none" r="27" stroke="#6f7ce8" strokeWidth="3" />
      <path d={`M${139 + offset} 88 H208 V132`} fill="none" stroke="#6f7ce8" strokeWidth="2" />
      <text fill="#b9c5ff" fontFamily="monospace" fontSize="13" x="214" y="137">SYNC LOCK</text>
    </svg>
  );
}

function VisualCameraPanel({ context }: { readonly context: ViewerPanelRenderContext }): JSX.Element {
  const index = Number(context.stream.id.split('-').at(-1) ?? 0);
  const unavailable = context.panel.state === 'missing' || context.panel.state === 'unsupported';
  const partial = context.panel.state === 'partial';
  return (
    <article
      aria-labelledby={`${context.panel.panelId}-visual-title`}
      className="viewer-panel"
      data-panel-state={context.panel.state}
    >
      <header>
        <h3 id={`${context.panel.panelId}-visual-title`}>
          <i className={partial ? styles.cameraDotError : styles.cameraDot} aria-hidden="true" />
          {context.stream.displayName}
        </h3>
        <span>{partial ? '丢失 3 帧 · 30.00 fps' : unavailable ? '流缺失' : '812 帧 · 30.00 fps'}</span>
      </header>
      {unavailable || partial ? (
        <div className={styles.missingFrame} role="status">
          <CameraOff aria-hidden="true" size={30} />
          <strong>{unavailable ? '媒体流缺失' : '局部帧缺失'}</strong>
          <span>{unavailable ? '数据清单已声明，当前未返回预览' : '共享时间轴保留缺口位置'}</span>
        </div>
      ) : <RobotTelemetryScene index={index} />}
      <CameraClockBadge clock={context.clock} />
    </article>
  );
}

function statusFor(index: number, scenario: RawDiagnosticVisualScenario): StreamDescriptor['availability'] {
  if (scenario === 'missing-slow') {
    if (index === 0) return 'missing';
    if (index === 1) return 'media-preparing';
  }
  if (scenario === 'reference' && index === 2) return 'partial';
  return 'ready';
}

function buildFixture(cameraCount: number, scenario: RawDiagnosticVisualScenario) {
  const manifest: RuntimeManifestDiscoveryProjection = {
    source: 'MANIFEST',
    read_only: true,
    cameras: Array.from({ length: cameraCount }, (_, index) => ({
      camera_id: cameraName(index),
      topic: `/sensors/camera_${index + 1}/image`,
      encoding: index === 3 ? 'depth16' : 'h264',
      frame_id: `camera_${index + 1}_optical`,
    })),
    topics: [],
    missing_expected_topics: [],
  };
  const mediaStreamsByTopic = Object.fromEntries(manifest.cameras.map((camera, index) => [
    camera.topic,
    {
      id: `visual-camera-${index}`,
      canonicalPath: camera.topic,
      displayName: camera.camera_id,
      modality: index === 3 ? 'depth' : 'rgb',
      schema: { id: index === 3 ? 'depth-image' : 'rgb-image', version: 'visual-only', encoding: camera.encoding ?? undefined },
      rateHz: 30,
      startNs: '0',
      endNs: '17100000000',
      availability: statusFor(index, scenario),
    } satisfies StreamDescriptor,
  ]));
  return { manifest, mediaStreamsByTopic };
}

const signalTracks: readonly ViewerTimelineTrack[] = [
  {
    id: 'joint-state',
    label: '关节状态',
    segments: [{ id: 'joint-ok', label: '7 关节 · 30 Hz', startNs: '0', endNs: '17100000000', tone: 'signal' }],
  },
  {
    id: 'action-command',
    label: '动作指令',
    segments: [
      { id: 'action-a', label: '接近', startNs: '1200000000', endNs: '5400000000', tone: 'action' },
      { id: 'action-b', label: '抓取', startNs: '6100000000', endNs: '10600000000', tone: 'action' },
      { id: 'action-c', label: '撤回', startNs: '11800000000', endNs: '16400000000', tone: 'action' },
    ],
  },
  {
    id: 'automatic-quality',
    label: '自动质检状态',
    segments: [
      { id: 'qc-pass-a', label: '自动检查', startNs: '0', endNs: '17100000000', tone: 'quality-pass' },
      { id: 'qc-risk-a', label: '时间戳回退', startNs: '7200000000', tone: 'issue' },
      { id: 'qc-risk-b', label: '局部缺帧', startNs: '11900000000', tone: 'issue' },
    ],
  },
];

function RawDiagnosticVisualFixture({
  cameraCount = 4,
  scenario = 'reference',
}: RawDiagnosticVisualFixtureOptions): JSX.Element {
  const [note, setNote] = useState('');
  const clockRef = useRef<ReturnType<typeof createPlaybackClock> | null>(null);
  if (!clockRef.current) {
    clockRef.current = createPlaybackClock({ startNs: '0', endNs: '17100000000' });
    clockRef.current.seek('7632000000');
  }
  useEffect(() => () => clockRef.current?.dispose(), []);
  const fixture = buildFixture(scenario === 'empty' ? 0 : cameraCount, scenario);

  return (
    <RawDiagnosticWorkbench
      id="e06-visual-fixture"
      title="Raw 诊断"
      description="采集记录 / 数据包诊断 / Raw 证据"
      clock={clockRef.current}
      manifest={fixture.manifest}
      mediaStreamsByTopic={fixture.mediaStreamsByTopic}
      collectionItems={[{
        id: 'visual-package-1',
        label: 'VIS-PACKAGE-001',
        description: '视觉测试专用确定性数据',
        status: '自动质检异常',
        statusTone: 'error',
        facts: [
          { label: '开始', value: '16:10:00' },
          { label: '结束', value: '16:10:17' },
          { label: '时长', value: '17.100 s', technical: true },
          { label: '帧数', value: '3,256 帧' },
          { label: '来源', value: '数据清单', technical: true },
        ],
      }]}
      selectedCollectionItemId="visual-package-1"
      findings={[
        {
          id: 'visual-finding-timestamp',
          title: '时间戳不连续',
          severity: 'error',
          streamLabel: '主臂相机',
          topic: '/sensors/camera_1/image',
          startNs: '7200000000',
          endNs: '9100000000',
          message: '检测到时间戳回退或跳变，可能影响跨模态对齐。',
          observed: '1.900 s',
          threshold: '≤ 33 ms',
        },
        {
          id: 'visual-finding-frame-gap',
          title: '左腕相机缺帧',
          severity: 'error',
          streamLabel: '左腕相机',
          topic: '/sensors/camera_3/image',
          startNs: '11800000000',
          endNs: '11900000000',
          message: '连续缺失 3 帧；其他相机与信号仍可使用。',
          observed: '100 ms',
          threshold: '≤ 33 ms',
        },
      ]}
      signalTracks={signalTracks}
      notes={{ value: note, onChange: setNote, maxLength: 500 }}
      commands={{
        preserveEvidence: { invoke: () => undefined },
        requestRecollection: { invoke: () => undefined },
        runAutomatedCheck: { invoke: () => undefined },
      }}
      slots={{ renderPanel: (context) => <VisualCameraPanel context={context} /> }}
    />
  );
}

const mountedRoots = new WeakMap<HTMLElement, Root>();

export function mountRawDiagnosticVisualFixture(
  host: HTMLElement,
  options: RawDiagnosticVisualFixtureOptions = {},
): void {
  mountedRoots.get(host)?.unmount();
  const root = createRoot(host);
  mountedRoots.set(host, root);
  root.render(<RawDiagnosticVisualFixture {...options} />);
}
