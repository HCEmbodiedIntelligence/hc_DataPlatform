import { Alert, Button, Drawer, Input, Select } from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import {
  Boxes,
  FileStack,
  ListChecks,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { DataSchemaVersion } from "../../entities/data-schema";
import {
  type CreateStreamSchemaInput,
  type DataSchemaValidationReport,
  useAssociateDataSchemaDatasetReference,
  useCreateStreamSchema,
  useDataSchemaDatasetReferences,
  useDataSchemas,
  useDataSchemaVersion,
  usePreflightDataSchemaPublish,
  usePublishDataSchema,
  useResolveDataSchemaRoute,
  useUpdateStreamSchemaDraft,
  useValidateStreamSchemaVersion,
} from "../../features/data-schemas/api";
import { schemaTelemetryProjection } from "../../features/data-schemas/registry-rules";
import { dataSchemasQueryCodec } from "../../features/data-schemas/routing";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import {
  DataCursorPager,
  DataTable,
  DetailTabs,
  FilterToolbar,
  PageState,
  StandardPageScaffold,
  StatusTag,
} from "../../shared/ui";
import workspace from "../ui-011e/workspace.module.css";
import { pageDataSchemasQueryCodec } from "./query-codec";

const detailTabs = [
  { id: "fields", label: "字段定义" },
  { id: "encoding", label: "编码" },
  { id: "compatibility", label: "兼容性" },
  { id: "references", label: "引用" },
] as const;

const registryTabs = [
  { id: "registry", label: "Schema Registry" },
  { id: "snapshots", label: "数据集快照" },
  { id: "compatibility", label: "兼容性检查" },
] as const;

const categories = [
  "全部",
  "图像",
  "深度",
  "点云",
  "关节状态",
  "动作",
  "位姿",
  "力与力矩",
  "IMU",
  "触觉",
] as const;

const logicalTypeForCategory: Readonly<
  Record<(typeof categories)[number], string | undefined>
> = {
  全部: undefined,
  图像: "IMAGE",
  深度: "DEPTH",
  点云: "POINT_CLOUD",
  关节状态: "JOINT_STATE",
  动作: "ACTION",
  位姿: "POSE",
  力与力矩: "WRENCH",
  IMU: "IMU",
  触觉: "TACTILE",
};

function categoryForLogicalType(
  logicalType: string | undefined,
): (typeof categories)[number] {
  return (
    categories.find(
      (category) => logicalTypeForCategory[category] === logicalType,
    ) ?? "全部"
  );
}

type SchemaEditorMode = "CREATE" | "IMPORT" | "EDIT";

interface ValidationProof {
  readonly schemaId: string;
  readonly schemaVersion: string;
  readonly etag: string;
  readonly report: DataSchemaValidationReport;
}

interface PublishProof {
  readonly schemaId: string;
  readonly schemaVersion: string;
  readonly etag: string;
  readonly idempotencyKey: string;
  readonly token: string;
}

const defaultDefinition = JSON.stringify(
  {
    fields: [
      {
        name: "payload",
        type: "bytes",
        description: "Channel payload",
        required: true,
      },
    ],
  },
  null,
  2,
);

function parseSchemaDefinition(
  raw: string,
): CreateStreamSchemaInput["schema_definition"] {
  const parsed: unknown = JSON.parse(raw);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("定义必须是包含 fields 数组的 JSON 对象。");
  }
  const fields = (parsed as Readonly<Record<string, unknown>>).fields;
  if (!Array.isArray(fields) || fields.length === 0) {
    throw new Error("定义至少需要一个 fields 条目。");
  }
  const names = new Set<string>();
  for (const field of fields) {
    if (typeof field !== "object" || field === null || Array.isArray(field)) {
      throw new Error("每个 fields 条目必须是对象。");
    }
    const name = (field as Readonly<Record<string, unknown>>).name;
    const type = (field as Readonly<Record<string, unknown>>).type;
    if (
      typeof name !== "string" ||
      !/^[A-Za-z][A-Za-z0-9_.-]*$/u.test(name) ||
      typeof type !== "string" ||
      !type.trim() ||
      names.has(name)
    ) {
      throw new Error("字段名必须唯一且合法，并且每个字段都需要 type。");
    }
    names.add(name);
  }
  return parsed as CreateStreamSchemaInput["schema_definition"];
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

function definitionRows(
  definition: Readonly<Record<string, unknown>>,
): readonly { name: string; type: string; description: string }[] {
  const fields = definition.fields;
  if (!Array.isArray(fields)) return [];
  return fields.flatMap((field) => {
    if (typeof field !== "object" || field === null) return [];
    const record = field as Readonly<Record<string, unknown>>;
    const text = (value: unknown, fallback: string) =>
      typeof value === "string" || typeof value === "number"
        ? String(value)
        : fallback;
    return [
      {
        name: text(record.name, "未命名"),
        type: text(record.type, "unknown"),
        description: text(record.description, "合同未提供说明"),
      },
    ];
  });
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = pageDataSchemasQueryCodec.parse(params);
  const [query, setQuery] = useState(search.q ?? "");
  const [category, setCategory] = useState<(typeof categories)[number]>(
    categoryForLogicalType(search.logicalType),
  );
  const [statusFilter, setStatusFilter] = useState<
    "ALL" | "DRAFT" | "PUBLISHED"
  >(search.status ?? "ALL");
  const [editorMode, setEditorMode] = useState<SchemaEditorMode>("CREATE");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [referenceOpen, setReferenceOpen] = useState(false);
  const [referenceError, setReferenceError] = useState<string | null>(null);
  const [datasetIdInput, setDatasetIdInput] = useState("");
  const [datasetVersionIdInput, setDatasetVersionIdInput] = useState("");
  const [schemaIdInput, setSchemaIdInput] = useState("");
  const [familyIdInput, setFamilyIdInput] = useState("");
  const [displayNameInput, setDisplayNameInput] = useState("");
  const [logicalTypeInput, setLogicalTypeInput] = useState("IMAGE");
  const [compatibilityModeInput, setCompatibilityModeInput] = useState<
    "STRICT" | "BACKWARD" | "FORWARD" | "FULL"
  >("BACKWARD");
  const [definitionInput, setDefinitionInput] = useState(defaultDefinition);
  const [changeSummaryInput, setChangeSummaryInput] = useState("");
  const [validationProof, setValidationProof] =
    useState<ValidationProof | null>(null);
  const [publishProof, setPublishProof] = useState<PublishProof | null>(null);
  const strictRoute = dataSchemasQueryCodec.parse(params);
  const relation =
    strictRoute.kind === "unresolved" || strictRoute.kind === "resolved"
      ? strictRoute.params
      : null;
  const mustResolveRelation = Boolean(search.componentId);
  const routeResolution = useResolveDataSchemaRoute(
    mustResolveRelation ? relation : null,
  );
  const list = useDataSchemas({
    ...(search.q ? { q: search.q } : {}),
    ...(search.status ? { status: search.status } : {}),
    ...(search.logicalType ? { logicalType: search.logicalType } : {}),
    ...(search.after ? { after: search.after } : {}),
    ...(search.before ? { before: search.before } : {}),
    limit: search.limit,
  });
  const detail = useDataSchemaVersion(
    search.schemaId ?? null,
    search.schemaVersion ?? null,
  );
  const items = list.data?.items ?? [];
  const listSelected =
    items.find(
      (item) =>
        item.schemaId === search.schemaId &&
        item.version === search.schemaVersion,
    ) ?? items[0];
  const selected = detail.data;
  const authoringTarget = selected ?? listSelected;
  const capabilities = useCapabilities();
  const create = useCreateStreamSchema();
  const update = useUpdateStreamSchemaDraft();
  const validate = useValidateStreamSchemaVersion();
  const preflight = usePreflightDataSchemaPublish();
  const publish = usePublishDataSchema();
  const datasetReferences = useDataSchemaDatasetReferences(
    authoringTarget?.schemaId ?? null,
    authoringTarget?.version ?? null,
    search.detailTab === "references",
  );
  const associateDatasetReference = useAssociateDataSchemaDatasetReference();
  const visibleDefinition =
    selected?.definition ?? listSelected?.definition ?? {};
  const fieldRows = definitionRows(visibleDefinition);

  const openEditor = (mode: SchemaEditorMode) => {
    setEditorMode(mode);
    setEditorError(null);
    setValidationProof(null);
    setPublishProof(null);
    if (mode === "EDIT" && authoringTarget) {
      setSchemaIdInput(authoringTarget.schemaId);
      setFamilyIdInput(authoringTarget.familyId);
      setDisplayNameInput(authoringTarget.displayName);
      setLogicalTypeInput(authoringTarget.logicalType);
      setCompatibilityModeInput(
        ["STRICT", "BACKWARD", "FORWARD", "FULL"].includes(
          authoringTarget.compatibilityMode,
        )
          ? (authoringTarget.compatibilityMode as typeof compatibilityModeInput)
          : "BACKWARD",
      );
      setDefinitionInput(JSON.stringify(authoringTarget.definition, null, 2));
      setChangeSummaryInput("");
    } else {
      setSchemaIdInput("");
      setFamilyIdInput("");
      setDisplayNameInput("");
      setLogicalTypeInput("IMAGE");
      setCompatibilityModeInput("BACKWARD");
      setDefinitionInput(defaultDefinition);
      setChangeSummaryInput("");
    }
    setEditorOpen(true);
  };

  const selectVersion = (schemaId: string, schemaVersion: string) => {
    setValidationProof(null);
    setPublishProof(null);
    setParams(
      pageDataSchemasQueryCodec.build(
        { ...search, schemaId, schemaVersion, componentId: undefined },
        search,
      ),
    );
  };

  const saveEditor = () => {
    const target = authoringTarget;
    try {
      const schemaDefinition = parseSchemaDefinition(definitionInput);
      const changeSummary = changeSummaryInput.trim();
      if (!changeSummary) throw new Error("请说明本次 Schema 变更。");
      if (editorMode === "EDIT") {
        if (!target || target.status !== "DRAFT") {
          throw new Error("只有当前草稿版本可以编辑。");
        }
        update.mutate(
          {
            schemaId: target.schemaId,
            schemaVersion: target.version,
            etag: target.etag,
            input: {
              display_name: displayNameInput.trim() || undefined,
              logical_type: logicalTypeInput.trim() || undefined,
              compatibility_mode: compatibilityModeInput,
              schema_definition: schemaDefinition,
              change_summary: changeSummary,
            },
            idempotencyKey: crypto.randomUUID(),
          },
          {
            onSuccess: (updated) => {
              setEditorOpen(false);
              selectVersion(updated.schemaId, updated.version);
            },
            onError: () =>
              setEditorError("保存失败；请检查版本状态并刷新后重试。"),
          },
        );
        return;
      }
      if (
        !schemaIdInput.trim() ||
        !familyIdInput.trim() ||
        !displayNameInput.trim()
      ) {
        throw new Error("请填写 Schema ID、Family ID 与展示名称。");
      }
      create.mutate(
        {
          source: editorMode === "IMPORT" ? "IMPORT" : "MANUAL",
          idempotencyKey: crypto.randomUUID(),
          input: {
            schema_id: schemaIdInput.trim(),
            family_id: familyIdInput.trim(),
            display_name: displayNameInput.trim(),
            logical_type: logicalTypeInput.trim(),
            compatibility_mode: compatibilityModeInput,
            schema_definition: schemaDefinition,
            change_summary: changeSummary,
          },
        },
        {
          onSuccess: (created) => {
            setEditorOpen(false);
            selectVersion(created.schemaId, created.version);
          },
          onError: () =>
            setEditorError("创建失败；请检查字段、权限与版本冲突。"),
        },
      );
    } catch (error) {
      setEditorError(
        error instanceof Error ? error.message : "Schema 定义无效。",
      );
    }
  };

  const validateCurrent = () => {
    if (!authoringTarget || authoringTarget.status !== "DRAFT") return;
    setPublishProof(null);
    validate.mutate(
      {
        schemaId: authoringTarget.schemaId,
        schemaVersion: authoringTarget.version,
        etag: authoringTarget.etag,
        idempotencyKey: crypto.randomUUID(),
      },
      {
        onSuccess: (report) =>
          setValidationProof({
            schemaId: authoringTarget.schemaId,
            schemaVersion: authoringTarget.version,
            etag: authoringTarget.etag,
            report,
          }),
      },
    );
  };

  const requestPreflight = () => {
    if (
      !authoringTarget ||
      !authoringTarget.hash ||
      !validationProof ||
      validationProof.schemaId !== authoringTarget.schemaId ||
      validationProof.schemaVersion !== authoringTarget.version ||
      validationProof.etag !== authoringTarget.etag ||
      validationProof.report.status !== "PASSED"
    )
      return;
    const idempotencyKey = crypto.randomUUID();
    preflight.mutate(
      {
        schemaId: authoringTarget.schemaId,
        schemaVersion: authoringTarget.version,
        etag: authoringTarget.etag,
        expectedHash: authoringTarget.hash.value,
        validationReportId: validationProof.report.id,
        compatibilityCheckId: validationProof.report.compatibility_check_id,
        changeSummary: "通过 Schema Registry 确认发布已校验的固定版本。",
        idempotencyKey,
      },
      {
        onSuccess: (result) => {
          if (!result.allowed || !result.preflight_token) return;
          setPublishProof({
            schemaId: authoringTarget.schemaId,
            schemaVersion: authoringTarget.version,
            etag: authoringTarget.etag,
            idempotencyKey,
            token: result.preflight_token,
          });
        },
      },
    );
  };

  const publishCurrent = () => {
    if (
      !authoringTarget ||
      !publishProof ||
      publishProof.schemaId !== authoringTarget.schemaId ||
      publishProof.schemaVersion !== authoringTarget.version ||
      publishProof.etag !== authoringTarget.etag
    )
      return;
    publish.mutate(
      {
        schemaId: authoringTarget.schemaId,
        schemaVersion: authoringTarget.version,
        etag: authoringTarget.etag,
        idempotencyKey: publishProof.idempotencyKey,
        preflightToken: publishProof.token,
      },
      {
        onSuccess: () => {
          setPublishProof(null);
          setValidationProof(null);
        },
      },
    );
  };

  const openDatasetReference = () => {
    setReferenceError(null);
    setDatasetIdInput("");
    setDatasetVersionIdInput("");
    setReferenceOpen(true);
  };

  const saveDatasetReference = () => {
    if (!authoringTarget || authoringTarget.status !== "PUBLISHED") {
      setReferenceError("只能为已发布的固定 Schema 版本记录数据集引用。");
      return;
    }
    const datasetId = datasetIdInput.trim();
    const datasetVersionId = datasetVersionIdInput.trim();
    if (!/^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/u.test(datasetId)) {
      setReferenceError("数据集 ID 必须使用 dataset_ 前缀。");
      return;
    }
    if (!/^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/u.test(datasetVersionId)) {
      setReferenceError("数据集版本 ID 必须使用 version_ 前缀。");
      return;
    }
    associateDatasetReference.mutate(
      {
        schemaId: authoringTarget.schemaId,
        schemaVersion: authoringTarget.version,
        etag: authoringTarget.etag,
        input: {
          dataset_id: datasetId,
          dataset_version_id: datasetVersionId,
        },
        idempotencyKey: crypto.randomUUID(),
      },
      {
        onSuccess: () => {
          setReferenceOpen(false);
          setReferenceError(null);
        },
        onError: (error) =>
          setReferenceError(
            isDomainError(error)
              ? error.message
              : "无法记录数据集引用；请刷新后重试。",
          ),
      },
    );
  };

  const columns = useMemo<ColumnDef<DataSchemaVersion, unknown>[]>(
    () => [
      {
        id: "displayName",
        header: "Schema 名称",
        size: 175,
        cell: ({ row }) => (
          <Button
            className={workspace.recordButton}
            type="link"
            onClick={() =>
              selectVersion(row.original.schemaId, row.original.version)
            }
          >
            {row.original.displayName}
          </Button>
        ),
      },
      {
        id: "logicalType",
        header: "逻辑类型",
        size: 105,
        cell: ({ row }) => row.original.logicalType,
      },
      {
        id: "version",
        header: "版本",
        size: 65,
        cell: ({ row }) => `v${row.original.version}`,
      },
      {
        id: "shape",
        header: "dtype / shape",
        size: 125,
        cell: ({ row }) =>
          definitionRows(row.original.definition)[0]?.type ?? "由定义决定",
      },
      {
        id: "mode",
        header: "兼容模式",
        size: 92,
        cell: ({ row }) => row.original.compatibilityMode,
      },
      {
        id: "status",
        header: "状态",
        size: 95,
        cell: ({ row }) => (
          <StatusTag
            status={row.original.status}
            label={
              row.original.status === "PUBLISHED"
                ? "已发布"
                : row.original.status
            }
            tone={
              row.original.status === "PUBLISHED"
                ? "success"
                : row.original.status === "UNKNOWN"
                  ? "warning"
                  : "neutral"
            }
          />
        ),
      },
    ],
    [search, setParams],
  );

  const pageState = list.isPending ? (
    <PageState state="loading" label="数据 Schema" />
  ) : list.error && isDomainError(list.error) ? (
    <PageState
      state={list.error.httpStatus === 403 ? "forbidden" : "error"}
      onRetry={() => void list.refetch()}
    />
  ) : mustResolveRelation && (strictRoute.kind === "not-found" || !relation) ? (
    <PageState
      state="not-found"
      title="Schema 深链无效"
      description="schemaId/schemaVersion/componentId 必须完整，版本会移除 v 前缀后验证。"
    />
  ) : mustResolveRelation && routeResolution.error ? (
    <PageState
      state="not-found"
      title="组件未引用此 Schema 版本"
      description="引用解析失败，不会替换为 latest/current。"
    />
  ) : null;

  return (
    <main className={workspace.page} data-page-id="P17">
      <StandardPageScaffold
        header={{
          title: "数据 Schema",
          description: "管理 Channel 数据结构、语义角色与固定版本兼容性。",
          breadcrumbs: [
            {
              key: "settings",
              label: "系统管理",
              to: "/settings/robot-models",
            },
            { key: "schemas", label: "数据 Schema" },
          ],
          actions: (
            <>
              <Button
                type="primary"
                disabled={!capabilities.has("data_schema.create")}
                onClick={() => openEditor("CREATE")}
              >
                新建 Schema
              </Button>
              <Button
                disabled={!capabilities.has("data_schema.import")}
                onClick={() => openEditor("IMPORT")}
              >
                导入定义
              </Button>
            </>
          ),
        }}
        summary={
          <div className={workspace.summaryStrip}>
            <SummaryItem
              icon={<Boxes size={20} />}
              label="Schema 版本"
              value={String(items.length)}
            />
            <SummaryItem
              icon={<ShieldCheck size={20} />}
              label="已发布"
              value={String(
                items.filter((item) => item.status === "PUBLISHED").length,
              )}
            />
            <SummaryItem
              icon={<ListChecks size={20} />}
              label="兼容"
              value={String(
                items.filter(
                  (item) =>
                    item.compatibilityResult &&
                    item.compatibilityResult !== "UNKNOWN",
                ).length,
              )}
            />
            <SummaryItem
              icon={<FileStack size={20} />}
              label="逻辑类型"
              value={String(
                new Set(items.map((item) => item.logicalType)).size,
              )}
            />
          </div>
        }
        filters={
          <>
            <nav aria-label="Schema 区域">
              <DetailTabs
                tabs={registryTabs}
                activeTab={search.tab}
                onChange={(tab) =>
                  setParams(
                    pageDataSchemasQueryCodec.build(
                      { ...search, tab: tab as typeof search.tab },
                      search,
                    ),
                  )
                }
              />
            </nav>
            <FilterToolbar
              onApply={() =>
                setParams(
                  pageDataSchemasQueryCodec.build(
                    {
                      ...search,
                      q: query || undefined,
                      status: statusFilter === "ALL" ? undefined : statusFilter,
                      logicalType: logicalTypeForCategory[category],
                      after: undefined,
                      before: undefined,
                    },
                    search,
                  ),
                )
              }
              onReset={() => {
                setQuery("");
                setCategory("全部");
                setStatusFilter("ALL");
                setParams(
                  pageDataSchemasQueryCodec.build(
                    {
                      ...search,
                      q: undefined,
                      status: undefined,
                      logicalType: undefined,
                      after: undefined,
                      before: undefined,
                    },
                    search,
                  ),
                );
              }}
            >
              <label className={workspace.toolbarField}>
                <span>Schema 名称</span>
                <Input.Search
                  className={workspace.toolbarSearch}
                  value={query}
                  placeholder="搜索 Schema 名称"
                  enterButton={
                    <Button
                      aria-label="搜索 Schema 名称"
                      icon={<Search aria-hidden="true" size={15} />}
                    />
                  }
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <label className={workspace.toolbarField}>
                <span>逻辑类型</span>
                <Select
                  value={category}
                  options={[
                    ...categories.map((value) => ({ value, label: value })),
                  ]}
                  onChange={(value) => setCategory(value as typeof category)}
                />
              </label>
              <label className={workspace.toolbarField}>
                <span>状态</span>
                <Select
                  value={statusFilter}
                  options={[
                    { value: "ALL", label: "全部" },
                    { value: "DRAFT", label: "草稿" },
                    { value: "PUBLISHED", label: "已发布" },
                  ]}
                  onChange={(value) =>
                    setStatusFilter(value as typeof statusFilter)
                  }
                />
              </label>
            </FilterToolbar>
          </>
        }
        state={pageState}
      >
        <div
          className={workspace.schemaPane}
          role="tabpanel"
          id={`tabpanel-${search.tab}`}
          aria-labelledby={`tab-${search.tab}`}
        >
          <nav className={workspace.categoryRail} aria-label="Schema 类别">
            <div className={workspace.categoryTitle}>Schema 类别</div>
            {categories.map((entry) => (
              <Button
                key={entry}
                type="text"
                className={`${workspace.categoryButton} ${entry === category ? workspace.categoryActive : ""}`}
                onClick={() => setCategory(entry)}
              >
                {entry}
              </Button>
            ))}
          </nav>

          <section className={workspace.pane} aria-label="Schema Registry">
            <header className={workspace.paneHeader}>
              <div>
                <h2>Schema Registry</h2>
                <p>{category} · 授权范围内固定版本</p>
              </div>
              <span className={workspace.inlineMeta}>共 {items.length} 项</span>
            </header>
            <div className={workspace.tableBody}>
              <DataTable
                data={items}
                columns={columns}
                getRowId={(item) => `${item.schemaId}:${item.version}`}
                caption="Schema Registry"
                state={items.length ? "ready" : "empty"}
                empty={
                  <PageState state={search.q ? "filtered-empty" : "empty"} />
                }
              />
            </div>
            {list.data ? (
              <footer className={workspace.tableFooter}>
                <DataCursorPager
                  pageInfo={{
                    startCursor: list.data.pageInfo.start_cursor,
                    endCursor: list.data.pageInfo.end_cursor,
                    hasPreviousPage: list.data.pageInfo.has_previous_page,
                    hasNextPage: list.data.pageInfo.has_next_page,
                  }}
                  onChange={(cursor) =>
                    setParams(
                      pageDataSchemasQueryCodec.build(
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

          <aside className={workspace.inspector} aria-label="Schema 版本详情">
            <header className={workspace.inspectorHeader}>
              <div>
                <h2>
                  {selected?.displayName ??
                    listSelected?.displayName ??
                    "固定版本详情"}
                </h2>
                <p>
                  {listSelected
                    ? `${listSelected.schemaId}/v${listSelected.version}`
                    : "选择稳定 schemaId/version"}
                </p>
              </div>
              <StatusTag
                status={selected?.status ?? listSelected?.status ?? "UNKNOWN"}
                label={
                  selected?.status === "PUBLISHED" ||
                  listSelected?.status === "PUBLISHED"
                    ? "已发布"
                    : (selected?.status ?? listSelected?.status)
                }
                tone={
                  selected?.status === "PUBLISHED" ||
                  listSelected?.status === "PUBLISHED"
                    ? "success"
                    : "warning"
                }
              />
            </header>
            <div className={workspace.inspectorBody}>
              <div className={workspace.inlineActions}>
                <Button
                  disabled={
                    authoringTarget?.status !== "DRAFT" ||
                    !capabilities.has("data_schema.create")
                  }
                  onClick={() => openEditor("EDIT")}
                >
                  编辑草稿
                </Button>
                <Button
                  disabled={
                    authoringTarget?.status !== "DRAFT" ||
                    !capabilities.has("data_schema.validate")
                  }
                  loading={validate.isPending}
                  onClick={validateCurrent}
                >
                  校验版本
                </Button>
                <Button
                  disabled={
                    authoringTarget?.status !== "DRAFT" ||
                    validationProof?.report.status !== "PASSED" ||
                    !capabilities.has("data_schema.publish")
                  }
                  loading={preflight.isPending}
                  onClick={requestPreflight}
                >
                  发布预检
                </Button>
                <Button
                  type="primary"
                  disabled={
                    !publishProof ||
                    authoringTarget?.status !== "DRAFT" ||
                    !capabilities.has("data_schema.publish")
                  }
                  loading={publish.isPending}
                  onClick={publishCurrent}
                >
                  发布版本
                </Button>
              </div>
              {validationProof ? (
                <Alert
                  type={
                    validationProof.report.status === "PASSED"
                      ? "success"
                      : "error"
                  }
                  showIcon
                  title={
                    validationProof.report.status === "PASSED"
                      ? "校验通过，可进行发布预检。"
                      : "校验未通过，修订草稿后再重试。"
                  }
                  description={
                    validationProof.report.findings.length
                      ? validationProof.report.findings
                          .map((finding) => finding.message)
                          .join("；")
                      : undefined
                  }
                />
              ) : null}
              <dl className={workspace.factList}>
                <div className={workspace.factRow}>
                  <dt>Schema ID</dt>
                  <dd>
                    <code>
                      {selected?.schemaId ?? listSelected?.schemaId ?? "—"}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>Family</dt>
                  <dd>
                    <code>
                      {selected?.familyId ?? listSelected?.familyId ?? "—"}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>Canonical hash</dt>
                  <dd>
                    <code>
                      {selected?.hash?.value ??
                        listSelected?.hash?.value ??
                        "—"}
                    </code>
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>兼容模式</dt>
                  <dd>
                    {selected?.compatibilityMode ??
                      listSelected?.compatibilityMode ??
                      "—"}
                  </dd>
                </div>
                <div className={workspace.factRow}>
                  <dt>遥测安全投影</dt>
                  <dd>
                    {selected
                      ? Object.keys(schemaTelemetryProjection(selected)).join(
                          "、",
                        )
                      : "固定 ID / 版本 / 状态"}
                  </dd>
                </div>
              </dl>
              <DetailTabs
                tabs={detailTabs.map((tab) => ({
                  ...tab,
                  id: `detail-${tab.id}`,
                }))}
                activeTab={`detail-${search.detailTab}`}
                panelIdForTab={(tabId) => `p17-detail-tabpanel-${tabId}`}
                onChange={(tabId) => {
                  const detailTab = tabId.replace(
                    /^detail-/u,
                    "",
                  ) as typeof search.detailTab;
                  setParams(
                    pageDataSchemasQueryCodec.build(
                      { ...search, detailTab },
                      search,
                    ),
                  );
                }}
              />
              {detailTabs.map((tab) => (
                <section
                  key={tab.id}
                  className={workspace.tabContent}
                  role="tabpanel"
                  id={`p17-detail-tabpanel-detail-${tab.id}`}
                  aria-labelledby={`tab-detail-${tab.id}`}
                  hidden={search.detailTab !== tab.id}
                >
                  {tab.id === "fields" ? (
                    fieldRows.length ? (
                      <table className={workspace.definitionTable}>
                        <thead>
                          <tr>
                            <th>字段名</th>
                            <th>类型</th>
                            <th>说明</th>
                          </tr>
                        </thead>
                        <tbody>
                          {fieldRows.map((field) => (
                            <tr key={field.name}>
                              <td>{field.name}</td>
                              <td>{field.type}</td>
                              <td>{field.description}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <PageState
                        state="empty"
                        title="定义中没有字段数组"
                        description="保留原始只读定义，不推导字段。"
                      />
                    )
                  ) : null}
                  {tab.id === "encoding" ? (
                    <pre
                      className={workspace.codeBlock}
                      tabIndex={0}
                      aria-label="Schema 定义，只读"
                    >
                      {JSON.stringify(visibleDefinition, null, 2)}
                    </pre>
                  ) : null}
                  {tab.id === "compatibility" ? (
                    <p className={workspace.safeNote}>
                      Mode：
                      {selected?.compatibilityMode ??
                        listSelected?.compatibilityMode ??
                        "—"}
                      ；结果：
                      {selected?.compatibilityResult ??
                        listSelected?.compatibilityResult ??
                        "NOT_RUN"}
                      。未知 verdict 必须只读。
                    </p>
                  ) : null}
                  {tab.id === "references" ? (
                    <section aria-label="数据集引用">
                      <div className={workspace.inlineActions}>
                        <Button
                          type="primary"
                          disabled={
                            authoringTarget?.status !== "PUBLISHED" ||
                            !capabilities.has("data_schema.publish") ||
                            !capabilities.has("datasets.read")
                          }
                          onClick={openDatasetReference}
                        >
                          关联数据集版本
                        </Button>
                      </div>
                      <p className={workspace.featureNote}>
                        每条记录固定到已发布 Schema 与 READY
                        数据集版本；后续草稿或新版本不会 改写此来源事实。
                      </p>
                      {datasetReferences.isPending ? (
                        <PageState state="loading" label="数据集引用" />
                      ) : datasetReferences.error ? (
                        <PageState
                          state={
                            isDomainError(datasetReferences.error) &&
                            datasetReferences.error.httpStatus === 403
                              ? "forbidden"
                              : "error"
                          }
                          title="无法读取数据集引用"
                          description={
                            isDomainError(datasetReferences.error)
                              ? datasetReferences.error.message
                              : "稍后重试或刷新当前范围。"
                          }
                          onRetry={() => void datasetReferences.refetch()}
                        />
                      ) : datasetReferences.data?.length ? (
                        <dl className={workspace.factList}>
                          {datasetReferences.data.map((reference) => (
                            <div
                              key={`${reference.dataset_id}:${reference.dataset_version_id}`}
                              className={workspace.factRow}
                            >
                              <dt>
                                <code>{reference.dataset_id}</code>
                              </dt>
                              <dd>
                                <code>{reference.dataset_version_id}</code>
                                <br />由 {reference.associated_by} 于{" "}
                                {new Date(
                                  reference.associated_at,
                                ).toLocaleString()}
                                固定引用
                              </dd>
                            </div>
                          ))}
                        </dl>
                      ) : (
                        <PageState
                          state="empty"
                          title="尚未关联数据集版本"
                          description="关联一个 READY 数据集版本以记录其使用的固定 Schema。"
                        />
                      )}
                    </section>
                  ) : null}
                </section>
              ))}
              <p className={workspace.warningNote} role="note">
                发布前必须使用当前 ETag
                完成服务端校验和一次性预检；已发布版本不可编辑。
              </p>
              <p className={workspace.safeNote}>
                Schema 原文只进入只读定义视图，永不进入遥测。
              </p>
            </div>
          </aside>
        </div>
        <Drawer
          destroyOnHidden
          open={editorOpen}
          placement="right"
          size="large"
          title={
            editorMode === "EDIT"
              ? "编辑 Schema 草稿"
              : editorMode === "IMPORT"
                ? "导入 Schema 定义"
                : "新建 Schema"
          }
          onClose={() => setEditorOpen(false)}
          extra={
            <Button
              type="primary"
              loading={create.isPending || update.isPending}
              onClick={saveEditor}
            >
              {editorMode === "EDIT" ? "保存草稿" : "创建草稿"}
            </Button>
          }
        >
          {editorError ? (
            <Alert
              type="error"
              showIcon
              title="无法保存 Schema"
              description={editorError}
              style={{ marginBottom: 16 }}
            />
          ) : null}
          <label className={workspace.toolbarField}>
            <span>Schema ID</span>
            <Input
              value={schemaIdInput}
              disabled={editorMode === "EDIT"}
              placeholder="camera-front"
              onChange={(event) => setSchemaIdInput(event.target.value)}
            />
          </label>
          <label className={workspace.toolbarField}>
            <span>Family ID</span>
            <Input
              value={familyIdInput}
              disabled={editorMode === "EDIT"}
              placeholder="camera"
              onChange={(event) => setFamilyIdInput(event.target.value)}
            />
          </label>
          <label className={workspace.toolbarField}>
            <span>展示名称</span>
            <Input
              value={displayNameInput}
              onChange={(event) => setDisplayNameInput(event.target.value)}
            />
          </label>
          <label className={workspace.toolbarField}>
            <span>逻辑类型</span>
            <Input
              value={logicalTypeInput}
              onChange={(event) => setLogicalTypeInput(event.target.value)}
            />
          </label>
          <label className={workspace.toolbarField}>
            <span>兼容模式</span>
            <Select
              value={compatibilityModeInput}
              options={[
                { value: "BACKWARD", label: "向后兼容" },
                { value: "FORWARD", label: "向前兼容" },
                { value: "FULL", label: "双向兼容" },
                { value: "STRICT", label: "严格不变" },
              ]}
              onChange={(value) =>
                setCompatibilityModeInput(
                  value as typeof compatibilityModeInput,
                )
              }
            />
          </label>
          <label className={workspace.toolbarField}>
            <span>定义 JSON</span>
            <Input.TextArea
              autoSize={{ minRows: 12, maxRows: 22 }}
              spellCheck={false}
              value={definitionInput}
              onChange={(event) => setDefinitionInput(event.target.value)}
            />
          </label>
          <label className={workspace.toolbarField}>
            <span>变更说明</span>
            <Input.TextArea
              autoSize={{ minRows: 3, maxRows: 6 }}
              value={changeSummaryInput}
              placeholder="说明这次创建、导入或草稿修订的目的。"
              onChange={(event) => setChangeSummaryInput(event.target.value)}
            />
          </label>
        </Drawer>
        <Drawer
          destroyOnHidden
          open={referenceOpen}
          placement="right"
          title="关联 READY 数据集版本"
          onClose={() => setReferenceOpen(false)}
          extra={
            <Button
              type="primary"
              loading={associateDatasetReference.isPending}
              onClick={saveDatasetReference}
            >
              固定引用
            </Button>
          }
        >
          {referenceError ? (
            <Alert
              type="error"
              showIcon
              title="无法固定数据集引用"
              description={referenceError}
              style={{ marginBottom: 16 }}
            />
          ) : null}
          <p className={workspace.featureNote}>
            {authoringTarget
              ? `${authoringTarget.schemaId}/v${authoringTarget.version}`
              : "请选择一个已发布 Schema 版本。"}
          </p>
          <label className={workspace.toolbarField}>
            <span>数据集 ID</span>
            <Input
              autoComplete="off"
              placeholder="dataset_camera_front"
              value={datasetIdInput}
              onChange={(event) => setDatasetIdInput(event.target.value)}
            />
          </label>
          <label className={workspace.toolbarField}>
            <span>数据集版本 ID</span>
            <Input
              autoComplete="off"
              placeholder="version_release_001"
              value={datasetVersionIdInput}
              onChange={(event) => setDatasetVersionIdInput(event.target.value)}
            />
          </label>
        </Drawer>
      </StandardPageScaffold>
    </main>
  );
}

export default Component;
