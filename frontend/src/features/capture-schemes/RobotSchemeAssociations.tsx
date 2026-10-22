import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useOrganizationCapabilities } from "../../shared/auth/use-capabilities";
import { captureApi, useCaptureMutation, useCaptureQuery } from "./api";
import {
  documentSchema,
  versionReference,
  type DeviceMapping,
  type Sample,
  type SchemeLink,
  type SchemeVersion,
} from "./contracts";
import { captureError, dataSummary, structureDiff } from "./presentation";
import {
  draftKey,
  readDraft,
  saveDraft,
  clearDraft,
  safeReturnTo,
  schemeLibraryPath,
} from "./navigation";
import { SamplePicker } from "./SamplePicker";
import { Differences, EvidenceView } from "./EvidenceView";

interface MappingDraft {
  schemeId: string;
  version: number | null;
  mapping: DeviceMapping;
  makeDefault: boolean;
  revision?: number;
  softwareFilter?: string;
}
export function RobotSchemeAssociations({
  organizationId,
  robotId,
  modelVersionId,
  canManage,
  allowedFormats,
  software,
  onReadyChange,
}: {
  organizationId: string;
  robotId: string;
  modelVersionId?: string | null;
  canManage: boolean;
  allowedFormats?: string[];
  software?: string;
  onReadyChange?: (ready: boolean) => void;
}) {
  const capability = useOrganizationCapabilities(organizationId);
  const manage = canManage && capability.has("robot.manage"),
    read = capability.has("robot.read");
  const location = useLocation();
  const back = location.pathname + location.search;
  const queryParams = new URLSearchParams(location.search);
  const previous = safeReturnTo(queryParams.get("returnTo"));
  const storage = draftKey(`mapping:${robotId}`, { organizationId });
  const [edit, setEdit] = useState<MappingDraft | null>(() =>
    readDraft<MappingDraft>(storage),
  );
  const [validating, setValidating] = useState<SchemeLink | null>(null);
  const [sample, setSample] = useState<Sample | null>(null);
  const [remove, setRemove] = useState<SchemeLink | null>(null);
  const links = useCaptureQuery(
    organizationId,
    "links",
    robotId,
    (signal) => captureApi.links(organizationId, robotId, signal),
    read,
  );
  const library = useCaptureQuery(
    organizationId,
    "list",
    {},
    (signal) => captureApi.list(organizationId, {}, signal),
    read,
  );
  const detail = useCaptureQuery(
    organizationId,
    "detail",
    edit?.schemeId,
    (signal) => captureApi.detail(organizationId, edit!.schemeId, signal),
    read && Boolean(edit?.schemeId),
  );
  const publishedIds = (library.data ?? [])
    .filter((scheme) => !scheme.archived && scheme.latest_version)
    .map((scheme) => scheme.scheme_id);
  const published = useCaptureQuery(
    organizationId,
    "published-options",
    publishedIds,
    (signal) =>
      Promise.all(
        publishedIds.map((id) => captureApi.detail(organizationId, id, signal)),
      ),
    read && Boolean(edit) && publishedIds.length > 0,
  );
  const existing = links.data?.find((l) => l.scheme_id === edit?.schemeId);
  const version =
    detail.data?.versions.find((v) => v.version === edit?.version) ??
    detail.data?.versions[0];
  const change = (next: MappingDraft | null) => {
    setEdit(next);
    if (next) saveDraft(storage, next);
    else clearDraft(storage);
  };
  const operation = useCaptureMutation(
    organizationId,
    async (
      intent:
        | { kind: "save" }
        | { kind: "validate"; link: SchemeLink }
        | { kind: "remove"; link: SchemeLink },
    ) => {
      if (intent.kind === "save") {
        if (!edit || !version) throw new Error("请选择已发布版本");
        await captureApi.associate(organizationId, robotId, {
          expected_revision: edit.revision ?? existing?.revision ?? 0,
          scheme: versionReference(version),
          mapping: {
            sources: Object.fromEntries(
              Object.entries(edit.mapping.sources)
                .filter(([format]) =>
                  version.adapters.some(
                    (adapter) => adapter.source_format === format,
                  ),
                )
                .map(([format, values]) => [
                  format,
                  Object.fromEntries(
                    Object.entries(values).filter(([name]) =>
                      sources.some(
                        (source) =>
                          source.format === format && source.name === name,
                      ),
                    ),
                  ),
                ]),
            ),
          },
          make_default: edit.makeDefault,
        });
        change(null);
      } else if (intent.kind === "validate") {
        if (!sample?.source_path) throw new Error("请选择当前实例的样本");
        await captureApi.validateMapping(
          organizationId,
          robotId,
          intent.link.scheme_id,
          intent.link.mapping_version,
          sample,
        );
      } else {
        await captureApi.remove(
          organizationId,
          robotId,
          intent.link.scheme_id,
          intent.link.revision,
        );
        setRemove(null);
      }
      const latest = await links.refetch();
      onReadyChange?.(
        Boolean(
          latest.data?.some(
            (l) => l.active && !l.archived && l.status === "READY",
          ),
        ),
      );
    },
  );
  const start = (link?: SchemeLink, upgrade = false) =>
    change({
      schemeId: link?.scheme_id ?? "",
      version: upgrade ? null : (link?.scheme_version ?? null),
      mapping: link?.mapping.mapping ?? { sources: {} },
      makeDefault:
        link?.is_default ?? !links.data?.some((l) => l.active && l.is_default),
      revision: link?.revision,
      softwareFilter: software ?? "",
    });
  const incompatibility = (v: SchemeVersion) => [
    ...(v.model_version_ids.length &&
    (!modelVersionId || !v.model_version_ids.includes(modelVersionId))
      ? ["型号不兼容"]
      : []),
    ...(allowedFormats &&
    !v.adapters.some((a) => allowedFormats.includes(a.source_format))
      ? ["上传格式不兼容"]
      : []),
    ...(software && v.software !== software ? ["采集软件不匹配"] : []),
  ];
  const choices = (published.data ?? [])
    .filter((scheme) => !scheme.archived)
    .flatMap((scheme) => {
      const candidates = scheme.versions.filter(
        (v) =>
          !edit?.softwareFilter ||
          v.software
            .toLocaleLowerCase()
            .includes(edit.softwareFilter.trim().toLocaleLowerCase()),
      );
      const candidate =
        candidates.find((v) => incompatibility(v).length === 0) ??
        candidates[0];
      if (!candidate) return [];
      const reasons = incompatibility(candidate);
      return [
        {
          value: scheme.scheme_id,
          version: candidate.version,
          disabled: reasons.length > 0,
          label: `${candidate.name} · v${candidate.version} · ${candidate.software}${reasons.length ? ` · ${reasons.join("、")}` : ""}`,
        },
      ];
    });
  const sources =
    version?.adapters
      .filter(
        (a) =>
          version.input_mode !== "AUTO" || a.source_format !== "CAPTURE_BUNDLE",
      )
      .flatMap<{ format: string; name: string; source: string }>((adapter) =>
        adapter.source_format === "LEROBOT_V3"
          ? Object.keys(version.output.features).map((name) => ({
              format: adapter.source_format,
              name,
              source: adapter.feature_sources[name] ?? name,
            }))
          : [
              {
                format: adapter.source_format,
                name: "state",
                source: adapter.configuration.state_topic ?? "",
              },
              {
                format: adapter.source_format,
                name: "action",
                source: adapter.configuration.action_topic ?? "",
              },
              ...adapter.configuration.cameras.map((c) => ({
                format: adapter.source_format,
                name: c.camera_id,
                source: c.topic,
              })),
              ...Object.entries(
                adapter.configuration.numeric_sources ?? {},
              ).map(([name, signal]) => ({
                format: adapter.source_format,
                name,
                source: signal.topic,
              })),
            ],
      ) ?? [];
  const verified =
    validating && links.data?.find((l) => l.scheme_id === validating.scheme_id);
  if (!read)
    return (
      <Alert type="warning" title="查看方案关联需要组织 robot.read 权限" />
    );
  return (
    <section aria-label="实例采集方案">
      <Space wrap>
        {previous && <Link to={previous}>返回来源页面</Link>}
        <Link to={schemeLibraryPath(back)}>前往方案库</Link>
        {manage && (
          <>
            <Button onClick={() => start()}>关联方案</Button>
            <Link to={schemeLibraryPath(back, undefined, true)}>
              新建方案并返回
            </Link>
          </>
        )}
      </Space>
      <p>
        可关联多个共享方案。默认项用于新任务预选；已有任务固定自己的版本和映射。共享编辑集中在方案库。
      </p>
      {!links.isPending &&
        !links.error &&
        !links.data?.some((l) => l.active) && (
          <Alert
            type="info"
            title="采集方案待配置"
            description="可先完成接入，关联并验证设备映射后再参与采集任务。"
          />
        )}
      {[
        links.error,
        library.error,
        detail.error,
        published.error,
        operation.error,
      ]
        .filter(Boolean)
        .map((error, i) => (
          <Alert
            key={i}
            type="error"
            title={captureError(error)}
            action={
              <Button
                onClick={() => {
                  void links.refetch();
                  void library.refetch();
                  if (edit) void published.refetch();
                }}
              >
                重新加载
              </Button>
            }
          />
        ))}
      <Table
        size="small"
        rowKey="scheme_id"
        loading={links.isFetching}
        dataSource={(links.data ?? []).filter((l) => l.active)}
        locale={{
          emptyText: links.error ? "方案关联读取失败" : "暂无方案关联",
        }}
        pagination={false}
        columns={[
          {
            title: "共享方案",
            render: (_, l) => (
              <Link to={schemeLibraryPath(back, l.scheme_id)}>
                {l.scheme.name}
              </Link>
            ),
          },
          {
            title: "版本",
            render: (_, l) =>
              `v${l.scheme_version} / 映射 ${l.mapping_version}`,
          },
          {
            title: "状态",
            render: (_, l) => (
              <>
                <Tag
                  color={
                    l.status === "READY" && !l.archived ? "green" : "orange"
                  }
                >
                  {l.archived
                    ? "方案已归档"
                    : l.status === "READY"
                      ? "可用于采集"
                      : "已关联 · 映射待验证"}
                </Tag>
                {l.is_default && <Tag>默认</Tag>}
              </>
            ),
          },
          {
            title: "操作",
            render: (_, l) => (
              <Space wrap>
                {manage && (
                  <>
                    <Button disabled={l.archived} onClick={() => start(l)}>
                      映射与默认项
                    </Button>
                    <Button
                      disabled={l.archived}
                      onClick={() => {
                        setValidating(l);
                        setSample(null);
                      }}
                    >
                      验证映射
                    </Button>
                    <Button
                      disabled={
                        l.archived ||
                        (library.data?.find((s) => s.scheme_id === l.scheme_id)
                          ?.latest_version ?? 0) <= l.scheme_version
                      }
                      onClick={() => start(l, true)}
                    >
                      升级版本
                    </Button>
                    <Button danger onClick={() => setRemove(l)}>
                      移除关联
                    </Button>
                  </>
                )}
              </Space>
            ),
          },
        ]}
      />
      {edit && manage && (
        <section
          style={{ padding: 16, border: "1px solid #ddd", marginTop: 16 }}
        >
          <h3>关联共享版本与设备来源</h3>
          <Form.Item label="按采集软件筛选已发布方案">
            <Input
              aria-label="采集软件筛选"
              placeholder="软件名称或版本"
              value={edit.softwareFilter ?? ""}
              onChange={(event) =>
                change({ ...edit, softwareFilter: event.target.value })
              }
            />
          </Form.Item>
          <Form.Item label="采集方案">
            <Select
              aria-label="关联采集方案"
              showSearch
              optionFilterProp="label"
              value={edit.schemeId || undefined}
              loading={published.isFetching}
              options={choices}
              onChange={(schemeId) =>
                change({
                  ...edit,
                  schemeId,
                  version:
                    choices.find((choice) => choice.value === schemeId)
                      ?.version ?? null,
                  mapping: { sources: {} },
                  revision: links.data?.find(
                    (link) => link.scheme_id === schemeId,
                  )?.revision,
                })
              }
            />
          </Form.Item>
          <Form.Item label="已发布版本">
            <Select
              aria-label="关联发布版本"
              value={version?.version}
              options={detail.data?.versions.map((v) => ({
                value: v.version,
                label: `v${v.version} · ${v.software} · ${dataSummary(v)}${incompatibility(v).length ? ` · ${incompatibility(v).join("、")}` : ""}`,
                disabled: incompatibility(v).length > 0,
              }))}
              onChange={(v) => change({ ...edit, version: v })}
            />
          </Form.Item>
          {version && (
            <>
              <p>{dataSummary(version)}</p>
              {incompatibility(version).map((reason) => (
                <Alert key={reason} type="warning" title={reason} />
              ))}
              {existing && (
                <details open={existing.scheme_version !== version.version}>
                  <summary>版本结构差异</summary>
                  <Differences
                    items={structureDiff(
                      documentSchema.parse(existing.scheme),
                      documentSchema.parse(version),
                    )}
                  />
                </details>
              )}
              <details
                open={
                  version.input_mode !== "AUTO" ||
                  Object.values(edit.mapping.sources).some(
                    (s) => Object.keys(s).length > 0,
                  )
                }
              >
                <summary>高级设备来源（留空沿用方案）</summary>
                {sources.map((source) => (
                  <Form.Item
                    key={`${source.format}:${source.name}`}
                    label={`${source.format === "MCAP" ? "MCAP 话题" : "LeRobot 字段"} · ${source.name}`}
                  >
                    <Input
                      aria-label={`${source.format} ${source.name} 设备来源`}
                      placeholder={source.source}
                      value={
                        edit.mapping.sources[source.format]?.[source.name] ?? ""
                      }
                      onChange={(e) => {
                        const map = { ...edit.mapping.sources[source.format] };
                        if (e.target.value) map[source.name] = e.target.value;
                        else delete map[source.name];
                        change({
                          ...edit,
                          mapping: {
                            sources: {
                              ...edit.mapping.sources,
                              [source.format]: map,
                            },
                          },
                        });
                      }}
                    />
                  </Form.Item>
                ))}
              </details>
            </>
          )}
          <Checkbox
            checked={edit.makeDefault}
            onChange={(e) => change({ ...edit, makeDefault: e.target.checked })}
          >
            设为新任务默认项
          </Checkbox>
          <p>
            留空来源沿用共享适配器。更改映射或升级将产生新映射版本，需要重新验证；新版已删除字段的设备来源随之移除，历史映射保持不变。
          </p>
          <Space>
            <Button
              disabled={
                !version || Boolean(version && incompatibility(version).length)
              }
              loading={operation.isPending}
              type="primary"
              onClick={() => operation.mutate({ kind: "save" })}
            >
              保存关联
            </Button>
            <Button onClick={() => change(null)}>取消关联编辑</Button>
          </Space>
        </section>
      )}
      <Modal
        open={Boolean(validating)}
        width={850}
        title="验证设备映射"
        footer={null}
        onCancel={() => setValidating(null)}
      >
        {validating && (
          <>
            <p>
              {validating.scheme.name} · v{validating.scheme_version} · 映射{" "}
              {validating.mapping_version}
            </p>
            <SamplePicker
              formats={validating.scheme.adapters.map((a) => a.source_format)}
              value={sample}
              onChange={setSample}
            />
            <Button
              disabled={!sample?.source_path || !manage}
              loading={operation.isPending}
              onClick={() =>
                operation.mutate({ kind: "validate", link: validating })
              }
            >
              验证设备样本
            </Button>
            <EvidenceView items={verified?.validations ?? []} />
            {operation.error && (
              <Alert type="error" title={captureError(operation.error)} />
            )}
          </>
        )}
      </Modal>
      <Modal
        open={Boolean(remove)}
        title="移除实例方案关联？"
        okText="移除关联"
        cancelText="取消"
        confirmLoading={operation.isPending}
        onCancel={() => setRemove(null)}
        onOk={() =>
          remove && operation.mutate({ kind: "remove", link: remove })
        }
      >
        <p>共享方案与历史任务快照继续保留。该实例不能再用此关联创建新计划。</p>
      </Modal>
    </section>
  );
}
