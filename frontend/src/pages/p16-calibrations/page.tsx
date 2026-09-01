import { Alert, Button, Drawer, Input, Select } from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import { Boxes, FileText, ListChecks, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { CalibrationSet } from "../../entities/calibration";
import {
  useCalibrationSet,
  useCalibrationSets,
  useCalibrationDatasetAssociations,
  useCalibrationValidationReport,
  useCalibrationVersionDocument,
  useCalibrationVersions,
  useCreateCalibrationSet,
  parseCalibrationDocumentInput,
  usePreflightCalibrationPublish,
  usePublishCalibration,
  useAssociateCalibrationDatasetVersion,
  useRecalibrateCalibrationSet,
  useValidateCalibrationVersion,
} from "../../features/calibrations/api";
import { resolveCalibrationFallback } from "../../features/calibrations/publish-rules";
import { calibrationsQueryCodec } from "../../features/calibrations/routing";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import {
  CursorPager,
  DataTable,
  DetailTabs,
  PageState,
  StandardPageScaffold,
  StatusTag,
} from "../../shared/ui";
import workspace from "../ui-011e/workspace.module.css";
import { pageCalibrationsQueryCodec } from "./query-codec";
import { CalibrationThreePreview } from "./components/CalibrationThreePreview";

const sectionTabs = [
  { id: "overview", label: "概览" },
  { id: "intrinsics", label: "坐标变换" },
  { id: "transforms", label: "Frame Graph" },
  { id: "timeCalibrations", label: "协方差" },
  { id: "jointCalibrations", label: "Fallback" },
] as const;

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

interface PublishProof {
  readonly setId: string;
  readonly version: string;
  readonly etag: string;
  readonly idempotencyKey: string;
  readonly token: string;
}

type CalibrationEditorMode = "CREATE" | "RECALIBRATE";

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = pageCalibrationsQueryCodec.parse(params);
  const routeResolution = calibrationsQueryCodec.parse(params);
  const routeParams =
    routeResolution.kind === "unresolved" || routeResolution.kind === "resolved"
      ? routeResolution.params
      : null;
  const [publishProof, setPublishProof] = useState<PublishProof | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editorMode, setEditorMode] = useState<CalibrationEditorMode>("CREATE");
  const [reportOpen, setReportOpen] = useState(false);
  const [associationOpen, setAssociationOpen] = useState(false);
  const [createSetId, setCreateSetId] = useState("");
  const [createRobotId, setCreateRobotId] = useState("");
  const [createComponentId, setCreateComponentId] = useState("");
  const [createSource, setCreateSource] = useState<"MANUAL" | "IMPORT">(
    "IMPORT",
  );
  const [createDocument, setCreateDocument] = useState("");
  const [recalibrationSummary, setRecalibrationSummary] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [datasetId, setDatasetId] = useState("");
  const [datasetVersionId, setDatasetVersionId] = useState("");
  const [associationError, setAssociationError] = useState<string | null>(null);
  const sets = useCalibrationSets(
    routeParams
      ? { robot_id: routeParams.robotId, component_id: routeParams.componentId }
      : {},
  );
  const selectedQuery = useCalibrationSet(routeParams?.setId ?? null);
  const items = sets.data?.items ?? [];
  const listSelected =
    items.find((item) => item.id === search.setId) ?? items[0];
  const selected = selectedQuery.data;
  const selectedSetId = selected?.id ?? listSelected?.id ?? null;
  const publicationTarget = selected ?? listSelected;
  const selectedVersion = search.version ?? publicationTarget?.version ?? null;
  const documentQuery = useCalibrationVersionDocument(
    selectedSetId,
    selectedVersion,
  );
  const versionsQuery = useCalibrationVersions(selectedSetId);
  const associationsQuery = useCalibrationDatasetAssociations(
    selectedSetId,
    selectedVersion,
  );
  const isCurrentVersion = Boolean(
    publicationTarget && selectedVersion === publicationTarget.version,
  );
  const reportId = isCurrentVersion
    ? (selected?.validation?.reportId ??
      listSelected?.validation?.reportId ??
      null)
    : null;
  const reportQuery = useCalibrationValidationReport(reportId);
  const capabilities = useCapabilities();
  const create = useCreateCalibrationSet();
  const validate = useValidateCalibrationVersion();
  const preflight = usePreflightCalibrationPublish();
  const publish = usePublishCalibration();
  const recalibrate = useRecalibrateCalibrationSet();
  const associateDataset = useAssociateCalibrationDatasetVersion();
  const canPreflight = Boolean(
    isCurrentVersion &&
      publicationTarget?.snapshotStatus === "DRAFT" &&
      publicationTarget.contentHash &&
      publicationTarget.validationContextHash &&
      publicationTarget.validation?.reportId &&
      capabilities.has("calibration.publish"),
  );
  const canPublish = Boolean(
    publishProof &&
      publicationTarget &&
      publishProof.setId === publicationTarget.id &&
      publishProof.version === publicationTarget.version &&
      publishProof.etag === publicationTarget.etag,
  );
  const canValidate = Boolean(
    isCurrentVersion &&
      publicationTarget?.snapshotStatus === "DRAFT" &&
      documentQuery.data &&
      capabilities.has("calibration.publish"),
  );

  const openCreateEditor = () => {
    setEditorMode("CREATE");
    setCreateSetId("");
    setCreateRobotId("");
    setCreateComponentId("");
    setCreateSource("IMPORT");
    setCreateDocument("");
    setRecalibrationSummary("");
    setCreateError(null);
    setCreateOpen(true);
  };

  const openRecalibrationEditor = () => {
    if (!publicationTarget || !documentQuery.data || !isCurrentVersion) return;
    setEditorMode("RECALIBRATE");
    setCreateSetId(publicationTarget.id);
    setCreateRobotId(publicationTarget.robotId);
    setCreateComponentId(publicationTarget.componentId ?? "");
    setCreateDocument(JSON.stringify(documentQuery.data.document, null, 2));
    setRecalibrationSummary("");
    setCreateError(null);
    setCreateOpen(true);
  };

  const openAssociationEditor = () => {
    setDatasetId("");
    setDatasetVersionId("");
    setAssociationError(null);
    setAssociationOpen(true);
  };

  const createDatasetAssociation = () => {
    if (!publicationTarget || !selectedVersion || !isCurrentVersion) return;
    if (!datasetId.trim() || !datasetVersionId.trim()) {
      setAssociationError("请填写要固定关联的真实数据集 ID 和数据集版本 ID。");
      return;
    }
    setAssociationError(null);
    associateDataset.mutate(
      {
        setId: publicationTarget.id,
        version: selectedVersion,
        etag: publicationTarget.etag,
        input: {
          dataset_id: datasetId.trim(),
          dataset_version_id: datasetVersionId.trim(),
        },
        idempotencyKey: crypto.randomUUID(),
      },
      {
        onSuccess: () => {
          setAssociationOpen(false);
          setDatasetId("");
          setDatasetVersionId("");
        },
      },
    );
  };

  const requestPreflight = () => {
    if (
      !publicationTarget ||
      !publicationTarget.contentHash ||
      !publicationTarget.validationContextHash ||
      !publicationTarget.validation?.reportId
    )
      return;
    const idempotencyKey = crypto.randomUUID();
    preflight.mutate(
      {
        setId: publicationTarget.id,
        version: publicationTarget.version,
        etag: publicationTarget.etag,
        expectedHash: publicationTarget.contentHash,
        validationContextHash: publicationTarget.validationContextHash,
        validationReportId: publicationTarget.validation.reportId,
        changeSummary: "通过 P16 标定管理页确认发布当前校验版本。",
        idempotencyKey,
      },
      {
        onSuccess: (result) => {
          if (!result.allowed || !result.preflight_token) return;
          setPublishProof({
            setId: publicationTarget.id,
            version: publicationTarget.version,
            etag: publicationTarget.etag,
            idempotencyKey,
            token: result.preflight_token,
          });
        },
      },
    );
  };

  const publishCurrent = () => {
    if (!publishProof) return;
    publish.mutate(
      {
        setId: publishProof.setId,
        version: publishProof.version,
        etag: publishProof.etag,
        idempotencyKey: publishProof.idempotencyKey,
        preflightToken: publishProof.token,
      },
      { onSuccess: () => setPublishProof(null) },
    );
  };

  const createCalibration = () => {
    let document: unknown;
    try {
      document = JSON.parse(createDocument) as unknown;
    } catch {
      setCreateError("导入内容必须是有效 JSON，且包含 frame_transforms。\n");
      return;
    }
    if (
      (editorMode === "CREATE" &&
        (!createSetId.trim() || !createRobotId.trim())) ||
      !createDocument.trim()
    ) {
      setCreateError("请填写标定集 ID、机器人 ID 和真实标定文档。");
      return;
    }
    const parsedDocument = parseCalibrationDocumentInput(document);
    if (!parsedDocument) {
      setCreateError(
        "标定文档必须包含至少一条有效 frame_transforms，且数值、四元数和协方差维度必须正确。",
      );
      return;
    }
    setCreateError(null);
    if (editorMode === "RECALIBRATE") {
      if (!publicationTarget || !recalibrationSummary.trim()) {
        setCreateError("重新标定必须说明已变更的真实测量事实。");
        return;
      }
      recalibrate.mutate(
        {
          setId: publicationTarget.id,
          etag: publicationTarget.etag,
          input: {
            document: parsedDocument,
            change_summary: recalibrationSummary.trim(),
          },
          idempotencyKey: crypto.randomUUID(),
        },
        {
          onSuccess: (successor) => {
            setPublishProof(null);
            setCreateOpen(false);
            setCreateError(null);
            setRecalibrationSummary("");
            setParams(
              pageCalibrationsQueryCodec.build(
                {
                  ...search,
                  robotId: successor.robotId,
                  componentId: successor.componentId ?? "component-unbound",
                  setId: successor.id,
                  version: successor.version,
                },
                search,
              ),
            );
          },
        },
      );
      return;
    }
    create.mutate(
      {
        input: {
          set_id: createSetId.trim(),
          robot_instance_id: createRobotId.trim(),
          component_id: createComponentId.trim() || null,
          source: createSource,
          document: parsedDocument,
        },
        idempotencyKey: crypto.randomUUID(),
      },
      {
        onSuccess: (created) => {
          setCreateOpen(false);
          setCreateDocument("");
          setCreateError(null);
          setParams(
            pageCalibrationsQueryCodec.build(
              {
                ...search,
                robotId: created.robotId,
                componentId: created.componentId ?? "component-unbound",
                setId: created.id,
                version: created.version,
              },
              search,
            ),
          );
        },
      },
    );
  };

  const validateCurrent = () => {
    if (!publicationTarget) return;
    validate.mutate({
      setId: publicationTarget.id,
      version: publicationTarget.version,
      etag: publicationTarget.etag,
      idempotencyKey: crypto.randomUUID(),
    });
  };

  const columns = useMemo<ColumnDef<CalibrationSet, unknown>[]>(
    () => [
      {
        id: "id",
        header: "标定集",
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
                    componentId:
                      row.original.componentId ?? "component-unbound",
                    setId: row.original.id,
                    version: undefined,
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
      {
        id: "version",
        header: "版本",
        size: 72,
        cell: ({ row }) => `v${row.original.version}`,
      },
      {
        id: "robot",
        header: "机器人",
        size: 130,
        cell: ({ row }) => row.original.robotId,
      },
      {
        id: "status",
        header: "数据状态",
        size: 90,
        cell: ({ row }) => (
          <StatusTag
            status={row.original.snapshotStatus}
            label={
              row.original.snapshotStatus === "READY"
                ? "Ready"
                : row.original.snapshotStatus
            }
            tone={
              row.original.snapshotStatus === "READY" ? "success" : "neutral"
            }
          />
        ),
      },
      {
        id: "availability",
        header: "Availability",
        size: 110,
        cell: ({ row }) => row.original.availability ?? "Draft 未生效",
      },
    ],
    [search, setParams],
  );

  const pageState = sets.isPending ? (
    <PageState state="loading" label="标定管理" />
  ) : sets.error && isDomainError(sets.error) ? (
    <PageState
      state={sets.error.httpStatus === 403 ? "forbidden" : "error"}
      onRetry={() => void sets.refetch()}
    />
  ) : routeResolution.kind === "not-found" ? (
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
  const publicationError = preflight.error ?? publish.error;
  const editorError = create.error ?? recalibrate.error;

  return (
    <main className={workspace.page} data-page-id="P16">
      <StandardPageScaffold
        header={{
          title: "标定管理",
          breadcrumbs: [
            {
              key: "settings",
              label: "系统管理",
              to: "/settings/robot-models",
            },
            { key: "calibrations", label: "标定管理" },
          ],
          actions: (
            <>
              <Button
                onClick={openCreateEditor}
                disabled={!capabilities.has("calibration.publish")}
              >
                导入或新建标定
              </Button>
              <Button
                onClick={openRecalibrationEditor}
                disabled={
                  !isCurrentVersion ||
                  !documentQuery.data ||
                  !capabilities.has("calibration.publish")
                }
              >
                重新标定
              </Button>
              <Button
                onClick={openAssociationEditor}
                disabled={
                  !isCurrentVersion ||
                  publicationTarget?.snapshotStatus !== "READY" ||
                  !capabilities.has("calibration.publish")
                }
              >
                关联数据版本
              </Button>
              <Button
                onClick={validateCurrent}
                disabled={!canValidate || validate.isPending}
                loading={validate.isPending}
              >
                校验当前版本
              </Button>
              <Button onClick={() => setReportOpen(true)} disabled={!reportId}>
                查看报告
              </Button>
              <Button
                onClick={requestPreflight}
                disabled={
                  !canPreflight || preflight.isPending || publish.isPending
                }
              >
                发布前检查
              </Button>
              <Button
                type="primary"
                onClick={publishCurrent}
                disabled={!canPublish || publish.isPending}
                loading={publish.isPending}
              >
                发布当前标定
              </Button>
            </>
          ),
        }}
        summary={
          <div className={workspace.summaryStrip}>
            <SummaryItem
              icon={<Boxes size={20} />}
              label="标定集"
              value={String(items.length)}
            />
            <SummaryItem
              icon={<ShieldCheck size={20} />}
              label="Ready"
              value={String(
                items.filter((item) => item.snapshotStatus === "READY").length,
              )}
            />
            <SummaryItem
              icon={<ListChecks size={20} />}
              label="校验通过"
              value={String(
                items.filter((item) => item.validation?.status === "PASSED")
                  .length,
              )}
            />
            <SummaryItem
              icon={<FileText size={20} />}
              label="固定版本"
              value={listSelected ? `v${listSelected.version}` : "—"}
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
              </div>
              <span className={workspace.inlineMeta}>共 {items.length} 项</span>
            </header>
            <div className={workspace.tableBody}>
              <DataTable
                data={items}
                columns={columns}
                getRowId={(item) => item.id}
                caption="标定集"
                state={items.length ? "ready" : "empty"}
                empty={<PageState state="empty" />}
              />
            </div>
            {sets.data ? (
              <footer className={workspace.tableFooter}>
                <CursorPager
                  pageInfo={{
                    startCursor: sets.data.pageInfo.start_cursor,
                    endCursor: sets.data.pageInfo.end_cursor,
                    hasPreviousPage: sets.data.pageInfo.has_previous_page,
                    hasNextPage: sets.data.pageInfo.has_next_page,
                  }}
                  onChange={(cursor) =>
                    setParams(
                      pageCalibrationsQueryCodec.build(
                        { ...search, ...cursor },
                        search,
                      ),
                    )
                  }
                  windowLabel={`当前 ${items.length} 项`}
                />
              </footer>
            ) : null}
          </section>

          <section className={workspace.pane} aria-label="标定版本工作区">
            <header className={workspace.paneHeader}>
              <div>
                <h2>{listSelected?.id ?? "固定标定版本"}</h2>
                <p>
                  {publicationTarget
                    ? `v${selectedVersion ?? publicationTarget.version} · ${publicationTarget.robotId}`
                    : "选择稳定 setId"}
                </p>
              </div>
              <StatusTag
                status={listSelected?.snapshotStatus ?? "UNKNOWN"}
                label={
                  listSelected?.snapshotStatus === "READY"
                    ? "Ready"
                    : listSelected?.snapshotStatus
                }
                tone={
                  listSelected?.snapshotStatus === "READY"
                    ? "success"
                    : "warning"
                }
              />
            </header>
            <div className={workspace.paneBody}>
              <div className={workspace.identityBar}>
                <dl className={workspace.identityFact}>
                  <dt>版本</dt>
                  <dd>{selectedVersion ? `v${selectedVersion}` : "—"}</dd>
                </dl>
                <dl className={workspace.identityFact}>
                  <dt>机器人</dt>
                  <dd>{listSelected?.robotId ?? "—"}</dd>
                </dl>
                <dl className={workspace.identityFact}>
                  <dt>组件</dt>
                  <dd>{listSelected?.componentId ?? "未绑定"}</dd>
                </dl>
                <dl className={workspace.identityFact}>
                  <dt>Availability</dt>
                  <dd>{listSelected?.availability ?? "Draft"}</dd>
                </dl>
              </div>
              <DetailTabs
                tabs={sectionTabs}
                activeTab={search.section}
                panelIdForTab={(tabId) => `p16-tabpanel-${tabId}`}
                onChange={(section) =>
                  setParams(
                    pageCalibrationsQueryCodec.build(
                      { ...search, section: section as typeof search.section },
                      search,
                    ),
                  )
                }
              />
              {sectionTabs.map((tab) => (
                <section
                  key={tab.id}
                  className={workspace.tabContent}
                  role="tabpanel"
                  id={`p16-tabpanel-${tab.id}`}
                  aria-labelledby={`tab-${tab.id}`}
                  hidden={search.section !== tab.id}
                >
                  {tab.id === "overview" ? (
                    <div className={workspace.visualStage}>
                      {documentQuery.isPending ? (
                        <PageState state="loading" label="真实标定坐标" />
                      ) : documentQuery.data ? (
                        <CalibrationThreePreview
                          document={documentQuery.data.document}
                        />
                      ) : documentQuery.error ? (
                        <PageState
                          state={
                            isDomainError(documentQuery.error) &&
                            documentQuery.error.httpStatus === 404
                              ? "empty"
                              : "error"
                          }
                          title="当前版本没有可用的真实标定文档"
                          description="不会根据内容哈希或机器人关系虚构坐标变换。"
                          onRetry={() => void documentQuery.refetch()}
                        />
                      ) : (
                        <div className={workspace.visualStageCopy}>
                          <Boxes aria-hidden="true" size={44} />
                          <strong>选择固定标定版本</strong>
                          <span>
                            选择后才会按需加载其服务端保存的坐标数据。
                          </span>
                        </div>
                      )}
                    </div>
                  ) : null}
                  {tab.id === "intrinsics" ? (
                    documentQuery.data ? (
                      <ul className={workspace.factList}>
                        {documentQuery.data.document.camera_intrinsics.map(
                          (camera) => (
                            <li
                              className={workspace.factRow}
                              key={camera.frame_id}
                            >
                              <strong>{camera.frame_id}</strong>
                              <span>
                                {camera.width_px} × {camera.height_px} · fx{" "}
                                {camera.fx_px} · fy {camera.fy_px}· cx{" "}
                                {camera.cx_px} · cy {camera.cy_px}
                              </span>
                            </li>
                          ),
                        )}
                      </ul>
                    ) : (
                      <PageState state="empty" title="请选择标定版本" />
                    )
                  ) : null}
                  {tab.id === "transforms" ? (
                    documentQuery.data ? (
                      <ul className={workspace.factList}>
                        {documentQuery.data.document.frame_transforms.map(
                          (transform) => (
                            <li
                              className={workspace.factRow}
                              key={`${transform.parent_frame}:${transform.child_frame}`}
                            >
                              <strong>
                                {transform.parent_frame} →{" "}
                                {transform.child_frame}
                              </strong>
                              <span>
                                t = [{transform.translation_m.join(", ")}] m · q
                                = [{transform.quaternion_xyzw.join(", ")}]
                              </span>
                            </li>
                          ),
                        )}
                      </ul>
                    ) : (
                      <PageState state="empty" title="请选择标定版本" />
                    )
                  ) : null}
                  {tab.id === "timeCalibrations" ? (
                    documentQuery.data ? (
                      <ul className={workspace.factList}>
                        {documentQuery.data.document.frame_transforms.map(
                          (transform) => (
                            <li
                              className={workspace.factRow}
                              key={`covariance-${transform.parent_frame}:${transform.child_frame}`}
                            >
                              <strong>
                                {transform.parent_frame} →{" "}
                                {transform.child_frame}
                              </strong>
                              <span>
                                {transform.covariance
                                  ? `6×6：${transform.covariance.join(", ")}`
                                  : "此真实变换未提供协方差矩阵"}
                              </span>
                            </li>
                          ),
                        )}
                      </ul>
                    ) : (
                      <PageState state="empty" title="请选择标定版本" />
                    )
                  ) : null}
                  {tab.id === "jointCalibrations" ? (
                    <p className={workspace.featureNote}>
                      {listSelected &&
                      resolveCalibrationFallback(listSelected, [listSelected])
                        .kind === "resolved"
                        ? "显式绑定优先；唯一候选才允许推导。"
                        : "Fallback 已阻断。"}
                    </p>
                  ) : null}
                </section>
              ))}
            </div>
          </section>

          <aside className={workspace.inspector} aria-label="标定详情">
            <header className={workspace.inspectorHeader}>
              <div>
                <h2>变换详情</h2>
                <p>{selected ? "固定版本 API 事实" : "列表级只读事实"}</p>
              </div>
              <StatusTag
                status={
                  selected?.validation?.status ??
                  listSelected?.validation?.status ??
                  "NOT_LOADED"
                }
                label={
                  selected?.validation?.status === "PASSED" ||
                  listSelected?.validation?.status === "PASSED"
                    ? "校验通过"
                    : "未加载详情"
                }
                tone={
                  selected?.validation?.status === "PASSED" ||
                  listSelected?.validation?.status === "PASSED"
                    ? "success"
                    : "neutral"
                }
              />
            </header>
            <div className={workspace.inspectorBody}>
              <dl className={workspace.factList}>
                <div className={workspace.factRow}>
                  <dt>Set ID</dt>
                  <dd>
                    <code>{selected?.id ?? listSelected?.id ?? "—"}</code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>Content hash</dt>
                  <dd>
                    <code>
                      {selected?.contentHash ??
                        listSelected?.contentHash ??
                        "—"}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>Context hash</dt>
                  <dd>
                    <code>
                      {selected?.validationContextHash ??
                        listSelected?.validationContextHash ??
                        "—"}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>报告 ID</dt>
                  <dd>
                    <code>
                      {selected?.validation?.reportId ??
                        listSelected?.validation?.reportId ??
                        "—"}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>发布状态</dt>
                  <dd>
                    {publicationTarget?.snapshotStatus === "READY"
                      ? "已发布"
                      : "草稿待发布前检查"}
                  </dd>
                </div>
              </dl>
              {publicationError ? (
                <Alert
                  type="error"
                  showIcon
                  message={
                    isDomainError(publicationError)
                      ? publicationError.message
                      : "标定发布请求失败；请保持当前版本并重新加载后再试。"
                  }
                />
              ) : null}
              {publishProof ? (
                <Alert
                  type="info"
                  showIcon
                  message="发布前检查已通过；证明仅绑定当前 ETag，发布后会立即失效。"
                />
              ) : null}
              {preflight.data?.blockers.length ? (
                <Alert
                  type="warning"
                  showIcon
                  message="发布前检查未通过"
                  description={preflight.data.blockers
                    .map((blocker) => blocker.message)
                    .join("；")}
                />
              ) : null}
              {validate.error ? (
                <Alert
                  type="error"
                  showIcon
                  message={
                    isDomainError(validate.error)
                      ? validate.error.message
                      : "服务端标定校验失败；当前草稿保持不变。"
                  }
                />
              ) : null}
              <section className={workspace.factList} aria-label="版本历史">
                <h3>版本历史</h3>
                {versionsQuery.isPending ? (
                  <span>正在加载版本历史…</span>
                ) : null}
                {versionsQuery.error ? (
                  <Button
                    type="link"
                    onClick={() => void versionsQuery.refetch()}
                  >
                    版本历史加载失败，重试
                  </Button>
                ) : null}
                {versionsQuery.data?.map((version) => (
                  <div className={workspace.factRow} key={version.version}>
                    <Button
                      type={
                        version.version === selectedVersion ? "primary" : "link"
                      }
                      onClick={() =>
                        setParams(
                          pageCalibrationsQueryCodec.build(
                            { ...search, version: version.version },
                            search,
                          ),
                        )
                      }
                      aria-pressed={version.version === selectedVersion}
                    >
                      v{version.version} · {version.source}
                    </Button>
                    <span>
                      {version.created_at} · <code>{version.content_hash}</code>
                    </span>
                  </div>
                ))}
              </section>
              <section
                className={workspace.factList}
                aria-label="关联的数据集版本"
              >
                <h3>关联的数据集版本</h3>
                {associationsQuery.isPending ? (
                  <span>正在加载关联…</span>
                ) : null}
                {associationsQuery.error ? (
                  <Button
                    type="link"
                    onClick={() => void associationsQuery.refetch()}
                  >
                    关联加载失败，重试
                  </Button>
                ) : null}
                {associationsQuery.data?.length === 0 ? (
                  <span>当前标定版本尚未关联任何真实数据集版本。</span>
                ) : null}
                {associationsQuery.data?.map((association) => (
                  <div
                    className={workspace.factRow}
                    key={`${association.dataset_id}:${association.dataset_version_id}`}
                  >
                    <strong>{association.dataset_id}</strong>
                    <span>
                      {association.dataset_version_id} ·{" "}
                      {association.associated_at}
                    </span>
                  </div>
                ))}
              </section>
            </div>
          </aside>
        </div>
      </StandardPageScaffold>
      <Drawer
        title={
          editorMode === "RECALIBRATE"
            ? "根据真实测量重新标定"
            : "导入或新建真实标定"
        }
        open={createOpen}
        onClose={() => {
          if (!create.isPending && !recalibrate.isPending) setCreateOpen(false);
        }}
        size="large"
        destroyOnHidden
        footer={
          <Button
            type="primary"
            onClick={createCalibration}
            loading={create.isPending || recalibrate.isPending}
            disabled={create.isPending || recalibrate.isPending}
          >
            {editorMode === "RECALIBRATE"
              ? "创建后继草稿"
              : "保存草稿并开始校验"}
          </Button>
        }
      >
        <Input
          aria-label="标定集 ID"
          placeholder="标定集 ID，例如 camera-front-20260821"
          value={createSetId}
          disabled={editorMode === "RECALIBRATE"}
          onChange={(event) => setCreateSetId(event.target.value)}
        />
        <Input
          aria-label="机器人 ID"
          placeholder="关联的机器人 ID"
          value={createRobotId}
          disabled={editorMode === "RECALIBRATE"}
          onChange={(event) => setCreateRobotId(event.target.value)}
          style={{ marginTop: 12 }}
        />
        <Input
          aria-label="组件 ID"
          placeholder="可选组件 ID"
          value={createComponentId}
          disabled={editorMode === "RECALIBRATE"}
          onChange={(event) => setCreateComponentId(event.target.value)}
          style={{ marginTop: 12 }}
        />
        {editorMode === "CREATE" ? (
          <Select
            aria-label="标定来源"
            value={createSource}
            onChange={setCreateSource}
            options={[
              { value: "IMPORT", label: "导入" },
              { value: "MANUAL", label: "手工录入" },
            ]}
            style={{ width: "100%", marginTop: 12 }}
          />
        ) : (
          <Input.TextArea
            aria-label="重新标定说明"
            placeholder="说明新的测量、设备或坐标变化"
            value={recalibrationSummary}
            onChange={(event) => setRecalibrationSummary(event.target.value)}
            autoSize={{ minRows: 2, maxRows: 5 }}
            style={{ marginTop: 12 }}
          />
        )}
        <Input.TextArea
          aria-label="标定 JSON 文档"
          placeholder={'{"frame_transforms":[...],"camera_intrinsics":[...]}'}
          value={createDocument}
          onChange={(event) => setCreateDocument(event.target.value)}
          autoSize={{ minRows: 14, maxRows: 24 }}
          spellCheck={false}
          style={{
            marginTop: 12,
            fontFamily: "var(--hc-font-mono, monospace)",
          }}
        />
        {createError ? (
          <Alert
            type="error"
            showIcon
            message={createError}
            style={{ marginTop: 12 }}
          />
        ) : null}
        {editorError ? (
          <Alert
            type="error"
            showIcon
            message={
              isDomainError(editorError)
                ? editorError.message
                : editorMode === "RECALIBRATE"
                  ? "重新标定失败；旧版本保持不变。"
                  : "保存草稿失败；未在浏览器构造成功结果。"
            }
            style={{ marginTop: 12 }}
          />
        ) : null}
      </Drawer>
      <Drawer
        title="关联真实数据集版本"
        open={associationOpen}
        onClose={() => {
          if (!associateDataset.isPending) setAssociationOpen(false);
        }}
        size="large"
        destroyOnHidden
        footer={
          <Button
            type="primary"
            onClick={createDatasetAssociation}
            loading={associateDataset.isPending}
            disabled={associateDataset.isPending}
          >
            固定关联
          </Button>
        }
      >
        <Input
          aria-label="数据集 ID"
          placeholder="dataset_…"
          value={datasetId}
          onChange={(event) => setDatasetId(event.target.value)}
        />
        <Input
          aria-label="数据集版本 ID"
          placeholder="version_…"
          value={datasetVersionId}
          onChange={(event) => setDatasetVersionId(event.target.value)}
          style={{ marginTop: 12 }}
        />
        {associationError ? (
          <Alert
            type="error"
            showIcon
            message={associationError}
            style={{ marginTop: 12 }}
          />
        ) : null}
        {associateDataset.error ? (
          <Alert
            type="error"
            showIcon
            message={
              isDomainError(associateDataset.error)
                ? associateDataset.error.message
                : "关联失败；标定版本与数据集版本均未被修改。"
            }
            style={{ marginTop: 12 }}
          />
        ) : null}
      </Drawer>
      <Drawer
        title="服务端校验报告"
        open={reportOpen}
        onClose={() => setReportOpen(false)}
        size="large"
        destroyOnHidden
      >
        {reportQuery.isPending ? (
          <PageState state="loading" label="校验报告" />
        ) : null}
        {reportQuery.data ? (
          <>
            <StatusTag
              status={reportQuery.data.status}
              label={
                reportQuery.data.status === "PASSED" ? "校验通过" : "校验失败"
              }
              tone={reportQuery.data.status === "PASSED" ? "success" : "danger"}
            />
            <p className={workspace.safeNote}>
              报告 {reportQuery.data.id} · {reportQuery.data.checked_at}
            </p>
            {reportQuery.data.findings.length ? (
              <ul className={workspace.factList}>
                {reportQuery.data.findings.map((finding) => (
                  <li
                    className={workspace.factRow}
                    key={`${finding.code}:${finding.path}`}
                  >
                    <strong>{finding.code}</strong>
                    <span>
                      {finding.path} · {finding.message}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <Alert type="success" showIcon title="服务端未发现校验问题。" />
            )}
          </>
        ) : null}
        {reportQuery.error ? (
          <PageState state="error" onRetry={() => void reportQuery.refetch()} />
        ) : null}
      </Drawer>
    </main>
  );
}

export default Component;
