import { memo, useState } from 'react';
import type { JSX, ReactNode } from 'react';
import {
  AlertTriangle,
  Bookmark,
  CircleAlert,
  ClipboardCopy,
  LockKeyhole,
  RefreshCw,
  RotateCcw,
  Video,
  Waves,
} from 'lucide-react';
import {
  formatElapsedNs,
  SharedSignalTimeline,
  ViewerMediaSurface,
  ViewerPlaybackControls,
} from './EpisodeWorkbenchCore';
import type {
  DataVisualizationWorkbenchAdapter,
  DataVisualizationWorkbenchSlots,
  WorkbenchAction,
  WorkbenchActionKind,
} from './workbench-contract';
import styles from './DataVisualizationWorkbench.module.css';

export interface DataVisualizationWorkbenchProps {
  readonly adapter: DataVisualizationWorkbenchAdapter;
  readonly slots?: DataVisualizationWorkbenchSlots;
  /** Hide the persistent collection rail when the caller exposes it through an overlay. */
  readonly showNavigation?: boolean;
}

const actionIcons: Readonly<Record<WorkbenchActionKind, ReactNode>> = {
  'preserve-evidence': <ClipboardCopy aria-hidden="true" size={16} />,
  'request-recollection': <RotateCcw aria-hidden="true" size={16} />,
  'run-automated-check': <RefreshCw aria-hidden="true" size={16} />,
  custom: <Bookmark aria-hidden="true" size={16} />,
};

function CollectionRailComponent({ adapter }: { readonly adapter: DataVisualizationWorkbenchAdapter }): JSX.Element {
  const selected = adapter.collectionItems.find((item) => item.id === adapter.selectedCollectionItemId)
    ?? adapter.collectionItems[0];
  return (
    <section className={styles.railSection} aria-labelledby={`${adapter.id}-collection-title`}>
      <header className={styles.sectionHeader}>
        <div>
          <span className={styles.eyebrow}>RAW PACKAGE</span>
          <h2 id={`${adapter.id}-collection-title`}>采集条目</h2>
        </div>
        <span className={styles.count}>{adapter.collectionItems.length}</span>
      </header>
      {adapter.collectionItems.length ? (
        <div className={styles.collectionList}>
          {adapter.collectionItems.map((item) => (
            <button
              aria-current={item.id === selected?.id ? 'true' : undefined}
              className={styles.collectionItem}
              data-status-tone={item.statusTone ?? 'neutral'}
              key={item.id}
              type="button"
              onClick={() => adapter.onSelectCollectionItem?.(item.id)}
            >
              <span className={styles.itemStatus}>{item.status}</span>
              <strong>{item.label}</strong>
              {item.description ? <small>{item.description}</small> : null}
            </button>
          ))}
        </div>
      ) : (
        <p className={styles.emptyState} role="status">当前范围没有可诊断的采集条目。</p>
      )}
      {selected?.facts?.length ? (
        <dl className={styles.factList}>
          {selected.facts.map((fact) => (
            <div key={`${fact.label}:${fact.value}`}>
              <dt>{fact.label}</dt>
              <dd className={fact.technical ? styles.technical : undefined} translate={fact.technical ? 'no' : undefined}>
                {fact.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  );
}

export const WorkbenchCollectionPanel = memo(CollectionRailComponent);

function FindingsInspectorComponent({ adapter }: { readonly adapter: DataVisualizationWorkbenchAdapter }): JSX.Element {
  const notes = adapter.notes;
  return (
    <section className={styles.inspectorSection} aria-labelledby={`${adapter.id}-findings-title`}>
      <header className={styles.sectionHeader}>
        <div>
          <span className={styles.eyebrow}>QUALITY EVIDENCE</span>
          <h2 id={`${adapter.id}-findings-title`}>质量发现</h2>
        </div>
        <span className={styles.findingCount}>{adapter.findings.length}</span>
      </header>
      {adapter.findings.length ? (
        <ol className={styles.findingList}>
          {adapter.findings.map((finding) => (
            <li key={finding.id}>
              <button
                type="button"
                className={styles.finding}
                data-severity={finding.severity}
                aria-label={`定位发现：${finding.title}`}
                onClick={() => adapter.clock.seek(finding.startNs)}
              >
                <span className={styles.findingTitle}>
                  <CircleAlert aria-hidden="true" size={15} />
                  <strong>{finding.title}</strong>
                  {finding.streamLabel ? <em>{finding.streamLabel}</em> : null}
                </span>
                <span className={styles.findingRange}>
                  {formatElapsedNs(BigInt(finding.startNs), BigInt(adapter.clock.startNs))}
                  {finding.endNs ? ` – ${formatElapsedNs(BigInt(finding.endNs), BigInt(adapter.clock.startNs))}` : ''}
                </span>
                <span className={styles.findingMessage}>{finding.message}</span>
                {finding.topic || finding.observed || finding.threshold ? (
                  <span className={styles.findingEvidence}>
                    {finding.topic ? <code translate="no">{finding.topic}</code> : null}
                    {finding.observed ? <span>观测 {finding.observed}</span> : null}
                    {finding.threshold ? <span>阈值 {finding.threshold}</span> : null}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <p className={styles.emptyState} role="status">当前时间范围没有质量发现。</p>
      )}
      {notes ? (
        <div className={styles.notes}>
          <label htmlFor={`${adapter.id}-diagnostic-note`}>诊断备注</label>
          <textarea
            autoComplete="off"
            id={`${adapter.id}-diagnostic-note`}
            maxLength={notes.maxLength ?? 500}
            name="diagnostic-note"
            placeholder="记录复现条件或证据说明…"
            readOnly={notes.readOnly || !notes.onChange}
            value={notes.value}
            onChange={(event) => notes.onChange?.(event.target.value)}
          />
          <div className={styles.notesMeta}>
            <span>备注只附加诊断上下文，不改变自动质检结论。</span>
            <output aria-label="诊断备注字数">{notes.value.length} / {notes.maxLength ?? 500}</output>
          </div>
        </div>
      ) : null}
    </section>
  );
}

const FindingsInspector = memo(FindingsInspectorComponent);

function ActionDock({ adapter }: { readonly adapter: DataVisualizationWorkbenchAdapter }): JSX.Element {
  const [runningId, setRunningId] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');

  const invoke = async (action: WorkbenchAction) => {
    if (!action.invoke || action.disabledReason || runningId) return;
    setRunningId(action.id);
    setAnnouncement(`${action.label}处理中…`);
    try {
      await action.invoke();
      setAnnouncement(`${action.label}已提交。`);
    } catch (error) {
      setAnnouncement(`${action.label}未完成：${error instanceof Error ? error.message : '请稍后重试。'}`);
    } finally {
      setRunningId(null);
    }
  };

  return (
    <section className={styles.actionDock} aria-label="Raw 诊断动作">
      {adapter.actions.map((action) => {
        const disabled = Boolean(action.disabledReason || !action.invoke || runningId);
        return (
          <div className={styles.actionItem} key={action.id}>
            <button
              type="button"
              disabled={disabled}
              aria-describedby={action.disabledReason ? `${adapter.id}-${action.id}-reason` : undefined}
              onClick={() => void invoke(action)}
            >
              {actionIcons[action.kind]}
              <span>{runningId === action.id ? `${action.label}…` : action.label}</span>
            </button>
            {action.disabledReason ? <small id={`${adapter.id}-${action.id}-reason`}>{action.disabledReason}</small> : null}
          </div>
        );
      })}
      <p className={styles.actionBoundary}>
        <LockKeyhole aria-hidden="true" size={14} />
        无人工 PASS；进入 Lance 只能由新的可审计自动结果决定。
      </p>
      <p className={styles.srAnnouncement} aria-live="polite">{announcement}</p>
    </section>
  );
}

export function DataVisualizationWorkbench({
  adapter,
  showNavigation = true,
  slots,
}: DataVisualizationWorkbenchProps): JSX.Element {
  const slotContext = { adapter };
  return (
    <section
      className={styles.workbench}
      data-camera-count={adapter.cameraStreams.length}
      data-mode={adapter.mode}
      data-navigation-visible={showNavigation}
      data-read-only={adapter.readOnly || undefined}
      aria-labelledby={`${adapter.id}-title`}
    >
      <header className={styles.workbenchHeader}>
        <div className={styles.titleBlock}>
          <span className={styles.eyebrow}>UNIFIED DATA VIEWER</span>
          <h1 id={`${adapter.id}-title`}>{adapter.title}</h1>
          {adapter.description ? <p>{adapter.description}</p> : null}
        </div>
        <span className={styles.readOnlyBadge}>
          <LockKeyhole aria-hidden="true" size={14} />
          {adapter.readOnly ? '只读诊断' : '可编辑'}
        </span>
      </header>
      {adapter.banner ? (
        <div className={styles.banner} data-tone={adapter.banner.tone} role="alert">
          <span className={styles.bannerLabel}>
            <AlertTriangle aria-hidden="true" size={16} />
            {adapter.banner.label}
          </span>
          <span className={styles.bannerMessage}>
            <strong>{adapter.banner.title}</strong>
            <small>{adapter.banner.description}</small>
          </span>
        </div>
      ) : null}
      {showNavigation ? (
        <section className={styles.navigation} aria-label="采集条目导航">
          {slots?.navigation ? (
            slots.navigation(slotContext)
          ) : (
            <WorkbenchCollectionPanel adapter={adapter} />
          )}
        </section>
      ) : null}
      <section className={styles.media} aria-label="相机与同步信号">
        <header className={styles.mediaHeader}>
          <span>
            <Video aria-hidden="true" size={16} />
            Manifest 相机
            <strong>{adapter.cameraStreams.length}</strong>
          </span>
          <span>
            <Waves aria-hidden="true" size={15} />
            单一共享光标
          </span>
        </header>
        {slots?.mediaHeader?.(slotContext)}
        <ViewerMediaSurface
          clock={adapter.clock}
          onResourceError={adapter.onResourceError}
          renderPanel={slots?.renderPanel}
          streams={adapter.cameraStreams}
        />
      </section>
      <section className={styles.inspector} aria-label="模式工具与发现">
        {slots?.inspector ? slots.inspector(slotContext) : <FindingsInspector adapter={adapter} />}
      </section>
      <section className={styles.timeline} aria-label="共享视频时间轴区域">
        <ViewerPlaybackControls clock={adapter.clock} />
        <SharedSignalTimeline
          clock={adapter.clock}
          disabled={adapter.readOnly || !adapter.onTimeRangeSelect}
          label="所有相机、关节状态、动作指令与自动质检共享的时间轴"
          selection={adapter.timelineSelection}
          tracks={adapter.timelineTracks}
          variant="signals"
          onRangeSelect={adapter.onTimeRangeSelect}
        />
      </section>
      <section className={styles.actions} aria-label="诊断动作边界">
        {slots?.actionDock ? slots.actionDock(slotContext) : <ActionDock adapter={adapter} />}
      </section>
      {adapter.cameraStreams.some((stream) => stream.availability === 'missing' || stream.availability === 'partial') ? (
        <p className={styles.streamNotice} role="status">
          <AlertTriangle aria-hidden="true" size={14} />
          局部缺流或缺帧不会清空其他相机与信号轨道。
        </p>
      ) : null}
      {adapter.mode === 'raw-diagnostic' && !adapter.readOnly ? (
        <p className={styles.contractError} role="alert">
          <CircleAlert aria-hidden="true" size={14} />
          Raw 诊断必须以只读方式呈现。
        </p>
      ) : null}
    </section>
  );
}
