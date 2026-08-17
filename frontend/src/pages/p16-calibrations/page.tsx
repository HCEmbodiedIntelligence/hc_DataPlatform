import { Button } from 'antd';
import type { ColumnDef } from '@tanstack/react-table';
import { Boxes, FileText, ListChecks, ShieldCheck } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { CalibrationSet } from '../../entities/calibration';
import {
  useCalibrationSet,
  useCalibrationSets,
  usePreflightCalibrationPublish,
  usePublishCalibration,
} from '../../features/calibrations/api';
import {
  canPublishCalibration,
  resolveCalibrationFallback,
  validateCovariance,
} from '../../features/calibrations/publish-rules';
import { calibrationsQueryCodec } from '../../features/calibrations/routing';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  DangerConfirmModal,
  DataCursorPager,
  DataTable,
  DetailTabs,
  PageState,
  StandardPageScaffold,
  StatusTag,
} from '../../shared/ui';
import workspace from '../ui-011e/workspace.module.css';
import { pageCalibrationsQueryCodec } from './query-codec';

const sectionTabs = [
  { id: 'overview', label: '概览' },
  { id: 'intrinsics', label: '坐标变换' },
  { id: 'transforms', label: 'Frame Graph' },
  { id: 'timeCalibrations', label: '协方差' },
  { id: 'jointCalibrations', label: 'Fallback' },
] as const;

interface PublishIntent {
  readonly key: string;
  readonly token: string;
  readonly impact: string;
  readonly preparedAt: string;
  readonly expiresAt: string;
  readonly resourceVersion: string;
}

function SummaryItem({
  icon,
  label,
  value,
}: Readonly<{ icon: React.ReactNode; label: string; value: string }>) {
  return (
    <section className={workspace.summaryItem} aria-label={label}>
      <span className={workspace.summaryIcon}>{icon}</span>
      <span className={workspace.summaryCopy}>
        <span>{label}</span>
        <strong>{value}</strong>
      </span>
    </section>
  );
}

function PrecisionView({ readOnly }: Readonly<{ readOnly: boolean }>) {
  const translation = ['0.000000000000', '0.000000000000', '0.000000000000'];
  const quaternion = ['0.000000000000', '0.000000000000', '0.000000000000', '1.000000000000'];
  return (
    <div>
      <p className={workspace.safeNote}>
        十进制值保持字符串精度；浏览器不做隐式浮点归一化，权威 hash 与校验由服务端生成。
      </p>
      <table className={workspace.definitionTable}>
        <thead>
          <tr>
            <th>分组</th>
            <th>X</th>
            <th>Y</th>
            <th>Z</th>
            <th>W</th>
            <th>模式</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Translation</td>
            {translation.map((value, index) => (
              <td key={`t-${index}`}>{value}</td>
            ))}
            <td>—</td>
            <td>{readOnly ? '只读' : '草稿'}</td>
          </tr>
          <tr>
            <td>Quaternion</td>
            {quaternion.map((value, index) => (
              <td key={`q-${index}`}>{value}</td>
            ))}
            <td>{readOnly ? '只读' : '草稿'}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = pageCalibrationsQueryCodec.parse(params);
  const routeResolution = calibrationsQueryCodec.parse(params);
  const routeParams =
    routeResolution.kind === 'unresolved' || routeResolution.kind === 'resolved'
      ? routeResolution.params
      : null;
  const sets = useCalibrationSets(
    routeParams ? { robot_id: routeParams.robotId, component_id: routeParams.componentId } : {},
  );
  const selectedQuery = useCalibrationSet(routeParams?.setId ?? null);
  const capabilities = useCapabilities();
  const scopeKey = useShellStore((state) => state.scopeKey);
  const preflight = usePreflightCalibrationPublish();
  const publish = usePublishCalibration();
  const [intent, setIntent] = useState<PublishIntent | null>(null);
  const intentKey = useRef<string | null>(null);
  const covariance = useMemo(
    () =>
      Array.from({ length: 36 }, (_, index) =>
        index % 7 === 0 ? '0.000001000000' : '0.000000000000',
      ),
    [],
  );
  const covarianceErrors = validateCovariance(covariance);
  const items = sets.data?.items ?? [];
  const listSelected = items.find((item) => item.id === search.setId) ?? items[0];
  const selected = selectedQuery.data;

  const evidence = selected?.validation
    ? { ...selected.validation, blockedReasons: selected.blockedReasons }
    : null;
  const decision = selected
    ? canPublishCalibration(selected, evidence)
    : { allowed: false, reasons: ['请选择固定标定集。'] };
  const canPublish = Boolean(
    selected && decision.allowed && capabilities.has('calibration.publish'),
  );

  const columns = useMemo<ColumnDef<CalibrationSet, unknown>[]>(
    () => [
      {
        id: 'id',
        header: '标定集',
        size: 190,
        cell: ({ row }) => (
          <Button
            className={workspace.recordButton}
            type="link"
            onClick={() =>
              setParams(
                pageCalibrationsQueryCodec.build(
                  {
                    ...search,
                    robotId: row.original.robotId,
                    componentId: row.original.componentId ?? 'component-unbound',
                    setId: row.original.id,
                  },
                  search,
                ),
              )
            }
          >
            {row.original.id}
          </Button>
        ),
      },
      { id: 'version', header: '版本', size: 72, cell: ({ row }) => `v${row.original.version}` },
      { id: 'robot', header: '机器人', size: 130, cell: ({ row }) => row.original.robotId },
      {
        id: 'status',
        header: '快照',
        size: 90,
        cell: ({ row }) => (
          <StatusTag
            status={row.original.snapshotStatus}
            label={row.original.snapshotStatus === 'READY' ? 'Ready' : row.original.snapshotStatus}
            tone={row.original.snapshotStatus === 'READY' ? 'success' : 'neutral'}
          />
        ),
      },
      {
        id: 'availability',
        header: 'Availability',
        size: 110,
        cell: ({ row }) => row.original.availability ?? 'Draft 未生效',
      },
    ],
    [search, setParams],
  );

  const startPreflight = () => {
    if (
      !selected?.contentHash ||
      !selected.validationContextHash ||
      !selected.validation ||
      !canPublish
    )
      return;
    const key = crypto.randomUUID();
    intentKey.current = key;
    preflight.mutate(
      {
        setId: selected.id,
        version: selected.version,
        etag: selected.etag,
        expectedHash: selected.contentHash,
        validationContextHash: selected.validationContextHash,
        validationReportId: selected.validation.reportId,
        changeSummary: '发布不可变标定版本',
        idempotencyKey: key,
      },
      {
        onSuccess(result) {
          if (
            !result.allowed ||
            !result.preflight_token ||
            !result.expires_at ||
            result.blockers.length
          ) {
            setIntent(null);
            return;
          }
          setIntent({
            key,
            token: result.preflight_token,
            impact:
              [...result.impacts, ...result.warnings].map((entry) => entry.message).join('；') ||
              'Ready 内容与 content hash 将不可变。',
            preparedAt: new Date().toISOString(),
            expiresAt: result.expires_at,
            resourceVersion: result.resource_revision,
          });
        },
      },
    );
  };

  const pageState =
    sets.isPending || capabilities.loading ? (
      <PageState state="loading" label="标定管理" />
    ) : sets.error && isDomainError(sets.error) ? (
      <PageState
        state={sets.error.httpStatus === 403 ? 'forbidden' : 'error'}
        onRetry={() => void sets.refetch()}
      />
    ) : routeResolution.kind === 'not-found' ? (
      <PageState
        state="not-found"
        title="标定深链无效"
        description={`未找到严格的 robot/component/set 关系：${routeResolution.reason}`}
      />
    ) : null;

  const relationMismatch = Boolean(
    selected &&
      routeParams &&
      (selected.robotId !== routeParams.robotId ||
        selected.componentId !== routeParams.componentId),
  );
  const effectiveState = relationMismatch ? (
    <PageState
      state="not-found"
      title="标定引用关系不存在"
      description="组件、机器人与标定集不属于同一权威关系；不会回退到 latest。"
    />
  ) : (
    pageState
  );

  return (
    <main className={workspace.page}>
      <StandardPageScaffold
        header={{
          title: '标定管理',
          description: '管理固定机器人坐标系、相机内外参与手眼标定版本。',
          breadcrumbs: [
            { key: 'settings', label: '系统管理', to: '/settings/robot-models' },
            { key: 'calibrations', label: '标定管理' },
          ],
          actions: (
            <>
              <Button disabled>导入标定</Button>
              <Button
                type="primary"
                disabled={!canPublish || preflight.isPending}
                loading={preflight.isPending}
                onClick={startPreflight}
              >
                新建 / 发布标定集
              </Button>
            </>
          ),
        }}
        summary={
          <div className={workspace.summaryStrip}>
            <SummaryItem icon={<Boxes size={20} />} label="标定集" value={String(items.length)} />
            <SummaryItem
              icon={<ShieldCheck size={20} />}
              label="Ready"
              value={String(items.filter((item) => item.snapshotStatus === 'READY').length)}
            />
            <SummaryItem
              icon={<ListChecks size={20} />}
              label="校验通过"
              value={String(items.filter((item) => item.validation?.status === 'PASSED').length)}
            />
            <SummaryItem
              icon={<FileText size={20} />}
              label="固定版本"
              value={listSelected ? `v${listSelected.version}` : '—'}
            />
          </div>
        }
        state={effectiveState}
      >
        <div className={workspace.threePane}>
          <section className={workspace.pane} aria-label="标定集列表">
            <header className={workspace.paneHeader}>
              <div>
                <h2>机器人 / 标定集</h2>
                <p>固定关系与服务端快照</p>
              </div>
              <span className={workspace.inlineMeta}>共 {items.length} 项</span>
            </header>
            <div className={workspace.tableBody}>
              <DataTable
                data={items}
                columns={columns}
                getRowId={(item) => item.id}
                caption="标定集"
                state={items.length ? 'ready' : 'empty'}
                empty={<PageState state="empty" />}
              />
            </div>
            {sets.data ? (
              <footer className={workspace.tableFooter}>
                <DataCursorPager
                  pageInfo={{
                    startCursor: sets.data.pageInfo.start_cursor,
                    endCursor: sets.data.pageInfo.end_cursor,
                    hasPreviousPage: sets.data.pageInfo.has_previous_page,
                    hasNextPage: sets.data.pageInfo.has_next_page,
                  }}
                  onChange={(cursor) =>
                    setParams(pageCalibrationsQueryCodec.build({ ...search, ...cursor }, search))
                  }
                  windowLabel={`当前 ${items.length} 项`}
                />
              </footer>
            ) : null}
          </section>

          <section className={workspace.pane} aria-label="标定版本工作区">
            <header className={workspace.paneHeader}>
              <div>
                <h2>{listSelected?.id ?? '固定标定版本'}</h2>
                <p>
                  {listSelected
                    ? `v${listSelected.version} · ${listSelected.robotId}`
                    : '选择稳定 setId'}
                </p>
              </div>
              <StatusTag
                status={listSelected?.snapshotStatus ?? 'UNKNOWN'}
                label={
                  listSelected?.snapshotStatus === 'READY' ? 'Ready' : listSelected?.snapshotStatus
                }
                tone={listSelected?.snapshotStatus === 'READY' ? 'success' : 'warning'}
              />
            </header>
            <div className={workspace.paneBody}>
              <div className={workspace.identityBar}>
                <dl className={workspace.identityFact}>
                  <dt>版本</dt>
                  <dd>{listSelected ? `v${listSelected.version}` : '—'}</dd>
                </dl>
                <dl className={workspace.identityFact}>
                  <dt>机器人</dt>
                  <dd>{listSelected?.robotId ?? '—'}</dd>
                </dl>
                <dl className={workspace.identityFact}>
                  <dt>组件</dt>
                  <dd>{listSelected?.componentId ?? '未绑定'}</dd>
                </dl>
                <dl className={workspace.identityFact}>
                  <dt>Availability</dt>
                  <dd>{listSelected?.availability ?? 'Draft'}</dd>
                </dl>
              </div>
              <DetailTabs
                tabs={sectionTabs}
                activeTab={search.section}
                onChange={(section) =>
                  setParams(
                    pageCalibrationsQueryCodec.build(
                      { ...search, section: section as typeof search.section },
                      search,
                    ),
                  )
                }
              />
              <section className={workspace.tabContent} role="tabpanel">
                {search.section === 'overview' ? (
                  <div className={workspace.visualStage}>
                    <div className={workspace.visualStageCopy}>
                      <Boxes aria-hidden="true" size={44} />
                      <strong>固定 Frame Graph</strong>
                      <span>
                        {listSelected
                          ? `world → base_link → ${listSelected.componentId ?? 'component'}`
                          : '选择标定集后检视固定关系'}
                      </span>
                    </div>
                  </div>
                ) : null}
                {search.section === 'intrinsics' ? (
                  <PrecisionView readOnly={listSelected?.snapshotStatus !== 'DRAFT'} />
                ) : null}
                {search.section === 'transforms' ? (
                  <p className={workspace.safeNote}>
                    Frame Graph 不是唯一事实源；方向为 parent →
                    child，循环与跨作用域引用由服务端校验。
                  </p>
                ) : null}
                {search.section === 'timeCalibrations' ? (
                  <div>
                    <h3>Covariance 6×6</h3>
                    <pre className={workspace.codeBlock}>
                      {covariance
                        .map((value, index) => `${value}${(index + 1) % 6 === 0 ? '\n' : '  '}`)
                        .join('')}
                    </pre>
                    {covarianceErrors.map((error) => (
                      <p className={workspace.warningNote} role="alert" key={error}>
                        {error}
                      </p>
                    ))}
                  </div>
                ) : null}
                {search.section === 'jointCalibrations' ? (
                  <p className={workspace.featureNote}>
                    {listSelected &&
                    resolveCalibrationFallback(listSelected, [listSelected]).kind === 'resolved'
                      ? '显式绑定优先；唯一候选才允许推导。'
                      : 'Fallback 已阻断。'}
                  </p>
                ) : null}
              </section>
            </div>
          </section>

          <aside className={workspace.inspector} aria-label="标定详情">
            <header className={workspace.inspectorHeader}>
              <div>
                <h2>变换详情</h2>
                <p>{selected ? '固定版本 API 事实' : '列表级只读事实'}</p>
              </div>
              <StatusTag
                status={
                  selected?.validation?.status ?? listSelected?.validation?.status ?? 'NOT_LOADED'
                }
                label={
                  selected?.validation?.status === 'PASSED' ||
                  listSelected?.validation?.status === 'PASSED'
                    ? '校验通过'
                    : '未加载详情'
                }
                tone={
                  selected?.validation?.status === 'PASSED' ||
                  listSelected?.validation?.status === 'PASSED'
                    ? 'success'
                    : 'neutral'
                }
              />
            </header>
            <div className={workspace.inspectorBody}>
              <dl className={workspace.factList}>
                <div className={workspace.factRow}>
                  <dt>Set ID</dt>
                  <dd>
                    <code>{selected?.id ?? listSelected?.id ?? '—'}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>Content hash</dt>
                  <dd>
                    <code>{selected?.contentHash ?? listSelected?.contentHash ?? '—'}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>Context hash</dt>
                  <dd>
                    <code>
                      {selected?.validationContextHash ??
                        listSelected?.validationContextHash ??
                        '—'}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>报告 ID</dt>
                  <dd>
                    <code>
                      {selected?.validation?.reportId ?? listSelected?.validation?.reportId ?? '—'}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>发布状态</dt>
                  <dd>
                    {selected
                      ? decision.allowed
                        ? '可发布'
                        : '只读 / 已阻断'
                      : '需固定 setId 详情'}
                  </dd>
                </div>
              </dl>
              {decision.reasons.map((reason) => (
                <p className={workspace.warningNote} role="note" key={reason}>
                  {reason}
                </p>
              ))}
              <div className={workspace.actionRow}>
                <Button disabled>3D 预览</Button>
                <Button disabled={!selected}>查看报告</Button>
              </div>
            </div>
          </aside>
        </div>
      </StandardPageScaffold>

      <DangerConfirmModal
        open={intent !== null}
        title="发布不可变标定版本"
        actionLabel="确认发布"
        resourceId={selected?.id ?? ''}
        impact={intent?.impact ?? '缺少预检证据'}
        blockers={selected?.blockedReasons ?? []}
        preflight={
          intent
            ? {
                preparedAt: intent.preparedAt,
                expiresAt: intent.expiresAt,
                resourceVersion: intent.resourceVersion,
                scopeKey,
              }
            : null
        }
        currentScopeKey={scopeKey}
        pending={publish.isPending}
        onCancel={() => {
          setIntent(null);
          intentKey.current = null;
        }}
        onConfirm={() => {
          if (!selected || !intent || intentKey.current !== intent.key) return;
          publish.mutate(
            {
              setId: selected.id,
              version: selected.version,
              etag: selected.etag,
              idempotencyKey: intent.key,
              preflightToken: intent.token,
            },
            {
              onSettled: () => {
                setIntent(null);
                intentKey.current = null;
              },
            },
          );
        }}
      />
    </main>
  );
}

export default Component;
