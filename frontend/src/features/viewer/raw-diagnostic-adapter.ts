import type { PlaybackClock } from './PlaybackClock';
import type { ViewerTimelineTrack } from './EpisodeWorkbenchCore';
import type { StreamDescriptor } from './types';
import type {
  DataVisualizationWorkbenchAdapter,
  WorkbenchAction,
  WorkbenchCollectionItem,
  WorkbenchDiagnosticNotes,
  WorkbenchFinding,
} from './workbench-contract';

/**
 * Narrow runtime projection from backend/openapi.generated.yaml.
 * The checked-in generated frontend type is currently stale and omits these
 * camera discovery fields, so this boundary stays local until regeneration.
 */
export interface RuntimeManifestDiscoveryProjection {
  readonly source: 'MANIFEST';
  readonly read_only: true;
  readonly cameras: readonly {
    readonly camera_id: string;
    readonly topic: string;
    readonly encoding?: string | null;
    readonly frame_id?: string | null;
  }[];
  readonly topics: readonly {
    readonly name: string;
    readonly required?: boolean;
    readonly schema_name?: string | null;
    readonly message_encoding?: string | null;
  }[];
  readonly missing_expected_topics: readonly string[];
}

export interface RawDiagnosticCommandPort {
  readonly invoke?: () => void | Promise<void>;
  readonly disabledReason?: string;
}

export interface RawDiagnosticAdapterInput {
  readonly id: string;
  readonly title?: string;
  readonly description?: string;
  readonly clock: PlaybackClock;
  readonly manifest: RuntimeManifestDiscoveryProjection;
  readonly mediaStreamsByTopic: Readonly<Record<string, StreamDescriptor | undefined>>;
  readonly collectionItems: readonly WorkbenchCollectionItem[];
  readonly selectedCollectionItemId?: string;
  readonly onSelectCollectionItem?: (id: string) => void;
  readonly findings: readonly WorkbenchFinding[];
  readonly signalTracks?: readonly ViewerTimelineTrack[];
  readonly notes?: WorkbenchDiagnosticNotes;
  readonly commands?: {
    readonly preserveEvidence?: RawDiagnosticCommandPort;
    readonly requestRecollection?: RawDiagnosticCommandPort;
    readonly runAutomatedCheck?: RawDiagnosticCommandPort;
  };
  readonly onResourceError?: DataVisualizationWorkbenchAdapter['onResourceError'];
}

function missingCameraStream(
  camera: RuntimeManifestDiscoveryProjection['cameras'][number],
  clock: PlaybackClock,
): StreamDescriptor {
  return {
    id: `manifest-camera:${camera.camera_id}`,
    canonicalPath: camera.topic,
    displayName: camera.camera_id,
    modality: camera.encoding?.toLowerCase().includes('depth') ? 'depth' : 'rgb',
    semanticRole: 'manifest-camera',
    schema: {
      id: camera.encoding ?? 'manifest-declared-camera',
      version: 'runtime-manifest/v1',
      encoding: camera.encoding ?? undefined,
    },
    startNs: clock.startNs,
    endNs: clock.endNs,
    frame: camera.frame_id ? { id: camera.frame_id, name: camera.frame_id } : undefined,
    availability: 'missing',
    accessibleSummary: `${camera.camera_id} 由 Manifest 声明，但当前预览流缺失。`,
  };
}

function cameraStreamsFromManifest(input: RawDiagnosticAdapterInput): readonly StreamDescriptor[] {
  return input.manifest.cameras.map((camera) => {
    const stream = input.mediaStreamsByTopic[camera.topic];
    if (!stream) return missingCameraStream(camera, input.clock);
    return {
      ...stream,
      displayName: camera.camera_id,
      canonicalPath: camera.topic,
      semanticRole: 'manifest-camera',
      frame: camera.frame_id ? { id: camera.frame_id, name: camera.frame_id } : stream.frame,
      schema: {
        ...stream.schema,
        encoding: camera.encoding ?? stream.schema.encoding,
      },
    };
  });
}

function cameraTimelineTracks(
  streams: readonly StreamDescriptor[],
  clock: PlaybackClock,
): readonly ViewerTimelineTrack[] {
  return streams.map((stream) => ({
    id: `camera:${stream.id}`,
    label: stream.displayName,
    segments: stream.availability === 'missing' || stream.availability === 'unsupported'
      ? []
      : [{
          id: `coverage:${stream.id}`,
          label: stream.availability === 'partial'
            ? '可用帧（存在缺口）'
            : stream.availability === 'preview-generating'
              ? '慢流缓冲中'
              : '视频覆盖',
          startNs: clock.startNs,
          endNs: clock.endNs,
          tone: 'signal',
        }],
  }));
}

function commandAction(
  id: string,
  kind: WorkbenchAction['kind'],
  label: string,
  port: RawDiagnosticCommandPort | undefined,
  fallbackDisabledReason: string,
): WorkbenchAction {
  return {
    id,
    kind,
    label,
    invoke: port?.invoke,
    disabledReason: port?.disabledReason ?? (port?.invoke ? undefined : fallbackDisabledReason),
  };
}

export function createRawDiagnosticWorkbenchAdapter(
  input: RawDiagnosticAdapterInput,
): DataVisualizationWorkbenchAdapter {
  const cameraStreams = cameraStreamsFromManifest(input);
  const actions: readonly WorkbenchAction[] = [
    commandAction(
      'preserve-evidence',
      'preserve-evidence',
      '复制证据链接',
      input.commands?.preserveEvidence,
      '当前上下文没有可复制的稳定证据链接。',
    ),
    commandAction(
      'request-recollection',
      'request-recollection',
      '请求重新采集',
      input.commands?.requestRecollection,
      '尚未提供经过授权的重采命令合同。',
    ),
    commandAction(
      'run-automated-check',
      'run-automated-check',
      '重新运行自动校验',
      input.commands?.runAutomatedCheck,
      '服务端未允许为此结果重新运行自动校验。',
    ),
  ];

  return {
    mode: 'raw-diagnostic',
    id: input.id,
    title: input.title ?? 'Raw 诊断',
    description: input.description ?? '按 Manifest 核对多相机、信号与自动质检证据。',
    readOnly: true,
    clock: input.clock,
    cameraStreams,
    collectionItems: input.collectionItems,
    selectedCollectionItemId: input.selectedCollectionItemId,
    onSelectCollectionItem: input.onSelectCollectionItem,
    findings: input.findings,
    timelineTracks: [
      ...cameraTimelineTracks(cameraStreams, input.clock),
      ...(input.signalTracks ?? []),
    ],
    notes: input.notes,
    actions,
    banner: {
      label: '自动质检异常',
      title: '异常数据保留在 Raw，不进入 Lance',
      description: '诊断备注和证据操作不会覆盖自动质检结论。',
      tone: 'error',
    },
    onResourceError: input.onResourceError,
  };
}
