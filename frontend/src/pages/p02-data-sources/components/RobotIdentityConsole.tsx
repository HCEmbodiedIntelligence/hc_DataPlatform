import {
  Alert,
  Button,
  Checkbox,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Skeleton,
  Tag,
  Typography,
} from "antd";
import {
  Bot,
  Check,
  ChevronRight,
  CircleOff,
  Copy,
  Fingerprint,
  KeyRound,
  RotateCcw,
  ShieldCheck,
  UploadCloud,
} from "lucide-react";
import { useState } from "react";
import type { IngestScope } from "../../../entities/data-source";
import {
  useIssueRobotCredential,
  useRevokeRobotCredential,
  useRobotAttemptHistory,
  useRobotCredentialHistory,
  useRobotIdentities,
  useRobotIdentityDetail,
  useRobotIdentityMutation,
  useRobotStatistics,
  useRobotUploadHistory,
} from "../../../features/robot-ingest/api";
import type {
  IssuedRobotCredential,
  RobotIdentity,
  RobotUploadPolicy,
} from "../../../features/robot-ingest/model";
import { useRobots } from "../../../features/robots/api";
import { isDomainError } from "../../../shared/api/domain-error";
import { routes } from "../../../features/ingest/routing";
import { formatStorageSize } from "../../../shared/lib/metric-presentation";
import styles from "../styles.module.css";

const formatOptions = [
  { label: "LeRobot v3", value: "LEROBOT_V3" },
  { label: "连续采集包", value: "CAPTURE_BUNDLE" },
  { label: "MCAP", value: "MCAP" },
];

const bytesPerGiB = 1024 ** 3;

interface PolicyDraft {
  readonly allowedFormats: readonly string[];
  readonly maxAssetGiB: number;
  readonly maxBatchGiB: number;
  readonly maxAssets: number;
  readonly partAuthorizationMinutes: number;
  readonly retentionHours: number;
  readonly requireSha256: boolean;
  readonly requireCrc64: boolean;
}

function draftFrom(identity: RobotIdentity): PolicyDraft {
  return {
    allowedFormats: identity.allowed_formats,
    maxAssetGiB: identity.upload_policy.max_asset_size_bytes / bytesPerGiB,
    maxBatchGiB: identity.upload_policy.max_batch_size_bytes / bytesPerGiB,
    maxAssets: identity.upload_policy.max_assets,
    partAuthorizationMinutes:
      identity.upload_policy.part_authorization_ttl_seconds / 60,
    retentionHours: identity.upload_policy.session_retention_hours,
    requireSha256: identity.upload_policy.require_sha256,
    requireCrc64: identity.upload_policy.require_crc64,
  };
}

function policyFrom(
  identity: RobotIdentity,
  draft: PolicyDraft,
): RobotUploadPolicy {
  return {
    ...identity.upload_policy,
    max_asset_size_bytes: Math.round(draft.maxAssetGiB * bytesPerGiB),
    max_batch_size_bytes: Math.round(draft.maxBatchGiB * bytesPerGiB),
    max_assets: draft.maxAssets,
    part_authorization_ttl_seconds: Math.round(
      draft.partAuthorizationMinutes * 60,
    ),
    session_retention_hours: draft.retentionHours,
    require_sha256: draft.requireSha256,
    require_crc64: draft.requireCrc64,
  };
}

function shortDate(value: string | null): string {
  if (!value) return "暂无";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function formatDuration(nanoseconds: number): string {
  const hours = nanoseconds / 3_600_000_000_000;
  if (hours >= 1) return `${hours.toFixed(hours >= 10 ? 0 : 1)} 小时`;
  return `${Math.round(nanoseconds / 60_000_000_000)} 分钟`;
}

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (!isDomainError(error)) return "操作未完成，请稍后重试。";
  return error.requestId
    ? `${error.message}（请求 ID：${error.requestId}）`
    : error.message;
}

function StatusDot({ active }: { readonly active: boolean }) {
  return (
    <span
      className={active ? styles.identityDotActive : styles.identityDotMuted}
      aria-hidden="true"
    />
  );
}

function AuthChain({ identity }: { readonly identity: RobotIdentity | null }) {
  const steps = [
    {
      icon: <Fingerprint size={17} />,
      label: "组织级身份",
      value: identity
        ? identity.state === "ENABLED"
          ? "已启用"
          : "已停用"
        : "选择机器人",
      active: identity?.state === "ENABLED",
    },
    {
      icon: <KeyRound size={17} />,
      label: "平台凭据",
      value: identity
        ? identity.credential_revision > 0
          ? `版本 v${identity.credential_revision}`
          : "待签发"
        : "—",
      active: Boolean(identity && identity.credential_revision > 0),
    },
    {
      icon: <ShieldCheck size={17} />,
      label: "任务解析",
      value: "Task ID 定位归属",
      active: true,
    },
    {
      icon: <UploadCloud size={17} />,
      label: "Raw 直传",
      value: "分片可恢复",
      active: true,
    },
  ];
  return (
    <div className={styles.identityChain} aria-label="机器人上传认证链">
      {steps.map((step, index) => (
        <div className={styles.identityChainStep} key={step.label}>
          <span className={styles.identityChainIcon} aria-hidden="true">
            {step.icon}
          </span>
          <span>
            <small>{step.label}</small>
            <strong>{step.value}</strong>
          </span>
          <StatusDot active={step.active} />
          {index < steps.length - 1 ? (
            <ChevronRight
              className={styles.identityChainArrow}
              size={15}
              aria-hidden="true"
            />
          ) : null}
        </div>
      ))}
    </div>
  );
}

function Metric({
  label,
  value,
}: {
  readonly label: string;
  readonly value: string;
}) {
  return (
    <div className={styles.robotMetric}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function RobotIdentityConsole({
  scope,
  canRead,
  canManage,
}: {
  readonly scope: IngestScope | null;
  readonly canRead: boolean;
  readonly canManage: boolean;
}) {
  const identities = useRobotIdentities(scope, canRead);
  const organizationRobots = useRobots({ lifecycle_status: "ACTIVE" });
  const [selectedIdentityId, setSelectedIdentityId] = useState<string | null>(
    null,
  );
  const [createOpen, setCreateOpen] = useState(false);
  const [createRobotId, setCreateRobotId] = useState<string | null>(null);
  const [createFormats, setCreateFormats] = useState<readonly string[]>([
    "LEROBOT_V3",
    "CAPTURE_BUNDLE",
    "MCAP",
  ]);
  const [policyDraft, setPolicyDraft] = useState<PolicyDraft | null>(null);
  const [credentialMode, setCredentialMode] = useState<
    "issue" | "rotate" | null
  >(null);
  const [credentialExpiry, setCredentialExpiry] = useState("");
  const [issuedCredential, setIssuedCredential] =
    useState<IssuedRobotCredential | null>(null);
  const [copied, setCopied] = useState(false);
  const [stateConfirmation, setStateConfirmation] = useState(false);
  const [revokeCredentialId, setRevokeCredentialId] = useState<string | null>(
    null,
  );

  const list = identities.data?.items ?? [];
  const boundRobotIds = new Set(list.map((identity) => identity.robot_id));
  const availableRobots = (organizationRobots.data?.items ?? []).filter(
    (robot) => !boundRobotIds.has(robot.id),
  );
  const selectedFromList =
    list.find(
      (identity) => identity.ingest_identity_id === selectedIdentityId,
    ) ??
    list[0] ??
    null;
  const effectiveIdentityId = selectedFromList?.ingest_identity_id ?? null;
  const detail = useRobotIdentityDetail(scope, effectiveIdentityId, canRead);
  const selectedIdentity = detail.data?.data ?? selectedFromList;
  const robotId = selectedIdentity?.robot_id ?? null;

  // These independent facts intentionally start together; React Query deduplicates them.
  const credentials = useRobotCredentialHistory(
    scope,
    effectiveIdentityId,
    canRead,
  );
  const uploads = useRobotUploadHistory(scope, robotId, canRead);
  const attempts = useRobotAttemptHistory(scope, robotId, canRead);
  const statistics = useRobotStatistics(scope, robotId, canRead);
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayStatistics = useRobotStatistics(scope, robotId, canRead, {
    createdFrom: todayStart.toISOString(),
  });

  const createIdentity = useRobotIdentityMutation("create");
  const updateIdentity = useRobotIdentityMutation("update");
  const enableIdentity = useRobotIdentityMutation("enable");
  const disableIdentity = useRobotIdentityMutation("disable");
  const issueCredential = useIssueRobotCredential();
  const revokeCredential = useRevokeRobotCredential();

  const enabledCount = list.filter(
    (identity) => identity.state === "ENABLED",
  ).length;
  const rejectedAttempts = (attempts.data?.items ?? []).filter(
    (attempt) => attempt.outcome === "REJECTED" || attempt.outcome === "FAILED",
  );
  const activeError =
    createIdentity.error ??
    updateIdentity.error ??
    enableIdentity.error ??
    disableIdentity.error ??
    issueCredential.error ??
    revokeCredential.error;

  if (!scope || !canRead) return null;

  return (
    <section
      className={styles.robotIdentityConsole}
      aria-labelledby="robot-identity-title"
    >
      <header className={styles.robotIdentityHeader}>
        <div>
          <span className={styles.robotIdentityKicker}>
            ROBOT AUTHENTICATION
          </span>
          <h2 id="robot-identity-title">
            <Bot size={21} aria-hidden="true" />
            机器人上传身份
          </h2>
          <p>
            身份归属组织，不永久绑定项目；每次上传由 collection_task_id
            解析项目、数据集与 Region。
          </p>
        </div>
        <Flex gap="small" align="center" wrap="wrap">
          <Tag color="blue">{enabledCount} 个已启用</Tag>
          {canManage ? (
            <Button
              type="primary"
              icon={<KeyRound size={16} aria-hidden="true" />}
              onClick={() => {
                createIdentity.reset();
                setCreateRobotId(null);
                setCreateOpen(true);
              }}
            >
              新建身份
            </Button>
          ) : null}
        </Flex>
      </header>

      <AuthChain identity={selectedIdentity} />

      {identities.isError ? (
        <Alert
          type="error"
          showIcon
          title="机器人上传身份暂不可用"
          description={errorMessage(identities.error)}
          action={
            <Button onClick={() => void identities.refetch()}>重试</Button>
          }
        />
      ) : identities.isPending ? (
        <Skeleton active paragraph={{ rows: 3 }} />
      ) : list.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="当前组织还没有机器人上传身份"
        >
          {canManage ? (
            <Button type="primary" onClick={() => setCreateOpen(true)}>
              创建第一个身份
            </Button>
          ) : null}
        </Empty>
      ) : (
        <div className={styles.robotIdentityWorkspace}>
          <div
            className={styles.robotIdentityList}
            role="list"
            aria-label="机器人身份"
          >
            {list.map((identity) => {
              const selected =
                identity.ingest_identity_id === effectiveIdentityId;
              return (
                <button
                  type="button"
                  role="listitem"
                  className={selected ? styles.robotIdentitySelected : ""}
                  key={identity.ingest_identity_id}
                  onClick={() =>
                    setSelectedIdentityId(identity.ingest_identity_id)
                  }
                >
                  <span className={styles.robotAvatar} aria-hidden="true">
                    <Bot size={19} />
                  </span>
                  <span className={styles.robotIdentityName}>
                    <strong>
                      {identity.display_name || identity.robot_id}
                    </strong>
                    <code>{identity.robot_id}</code>
                  </span>
                  <span className={styles.robotIdentityListState}>
                    <StatusDot active={identity.state === "ENABLED"} />
                    {identity.state === "ENABLED" ? "启用" : "停用"}
                  </span>
                </button>
              );
            })}
          </div>

          {selectedIdentity ? (
            <div className={styles.robotIdentityDetail}>
              <header className={styles.robotDetailHeader}>
                <div>
                  <strong>
                    {selectedIdentity.display_name || selectedIdentity.robot_id}
                  </strong>
                  <code>{selectedIdentity.ingest_identity_id}</code>
                </div>
                {canManage ? (
                  <Flex gap="small" wrap="wrap">
                    <Button
                      size="small"
                      onClick={() =>
                        setPolicyDraft(draftFrom(selectedIdentity))
                      }
                    >
                      格式与策略
                    </Button>
                    <Button
                      size="small"
                      danger={selectedIdentity.state === "ENABLED"}
                      onClick={() => setStateConfirmation(true)}
                    >
                      {selectedIdentity.state === "ENABLED" ? "停用" : "启用"}
                    </Button>
                  </Flex>
                ) : null}
              </header>

              <div className={styles.robotFactGrid}>
                <div>
                  <span>最近认证</span>
                  <strong>
                    {shortDate(selectedIdentity.last_authenticated_at)}
                  </strong>
                </div>
                <div>
                  <span>最近心跳</span>
                  <strong>{shortDate(selectedIdentity.last_seen_at)}</strong>
                </div>
                <div>
                  <span>最近上传</span>
                  <strong>{shortDate(selectedIdentity.last_upload_at)}</strong>
                </div>
                <div>
                  <span>允许格式</span>
                  <strong>
                    {selectedIdentity.allowed_formats.join(" · ")}
                  </strong>
                </div>
              </div>

              <section
                className={styles.robotStats}
                aria-label="机器人上传统计"
              >
                <Metric
                  label="上传批次"
                  value={String(statistics.data?.upload_batch_count ?? "—")}
                />
                <Metric
                  label="成功 Raw"
                  value={String(statistics.data?.committed_raw_count ?? "—")}
                />
                <Metric
                  label="技术失败"
                  value={String(
                    statistics.data?.technical_failure_count ?? "—",
                  )}
                />
                <Metric
                  label="Episode"
                  value={String(statistics.data?.episode_count ?? "—")}
                />
                <Metric
                  label="合格 Episode"
                  value={String(statistics.data?.qc_pass_count ?? "—")}
                />
                <Metric
                  label="风险 / 不合格"
                  value={
                    statistics.data
                      ? String(
                          statistics.data.qc_risk_count +
                            statistics.data.qc_reject_count,
                        )
                      : "—"
                  }
                />
                <Metric
                  label="今日验证数据量"
                  value={
                    todayStatistics.data
                      ? formatStorageSize(
                          String(todayStatistics.data.raw_bytes),
                        )
                      : "—"
                  }
                />
                <Metric
                  label="累计 Raw 数据量"
                  value={
                    statistics.data
                      ? formatStorageSize(String(statistics.data.raw_bytes))
                      : "—"
                  }
                />
                <Metric
                  label="QC 合格率"
                  value={
                    statistics.data?.qualified_rate == null
                      ? "未评估"
                      : `${(statistics.data.qualified_rate * 100).toFixed(1)}%`
                  }
                />
                <Metric
                  label="帧 / 样本"
                  value={
                    statistics.data
                      ? `${statistics.data.frame_count} / ${statistics.data.sample_count}`
                      : "—"
                  }
                />
                <Metric
                  label="总采集时长"
                  value={
                    statistics.data
                      ? formatDuration(statistics.data.capture_duration_ns)
                      : "—"
                  }
                />
              </section>

              <div className={styles.robotDetailColumns}>
                <section className={styles.robotDetailSection}>
                  <header>
                    <span>
                      <KeyRound size={15} aria-hidden="true" />
                      平台凭据
                    </span>
                    {canManage ? (
                      <Flex gap={4}>
                        <Button
                          type="link"
                          size="small"
                          onClick={() => setCredentialMode("issue")}
                        >
                          签发
                        </Button>
                        <Button
                          type="link"
                          size="small"
                          onClick={() => setCredentialMode("rotate")}
                        >
                          轮换
                        </Button>
                      </Flex>
                    ) : null}
                  </header>
                  <div className={styles.credentialHistory}>
                    {(credentials.data ?? []).slice(0, 4).map((credential) => (
                      <div key={credential.credential_id}>
                        <span>
                          <code>v{credential.credential_version}</code>
                          <small>{credential.token_prefix}…</small>
                        </span>
                        <span>
                          <Tag
                            color={
                              credential.state === "ACTIVE"
                                ? "green"
                                : "default"
                            }
                          >
                            {credential.state}
                          </Tag>
                          {canManage && credential.state === "ACTIVE" ? (
                            <Button
                              type="link"
                              danger
                              size="small"
                              onClick={() =>
                                setRevokeCredentialId(credential.credential_id)
                              }
                            >
                              撤销
                            </Button>
                          ) : null}
                        </span>
                      </div>
                    ))}
                    {!credentials.isPending &&
                    (credentials.data?.length ?? 0) === 0 ? (
                      <Typography.Text type="secondary">
                        尚未签发凭据
                      </Typography.Text>
                    ) : null}
                  </div>
                </section>

                <section className={styles.robotDetailSection}>
                  <header>
                    <span>
                      <UploadCloud size={15} aria-hidden="true" />
                      当前项目近期上传
                    </span>
                    <Button
                      type="link"
                      size="small"
                      href={`${routes.uploadRecords.build()}?robotId=${encodeURIComponent(selectedIdentity.robot_id)}`}
                    >
                      打开记录
                    </Button>
                  </header>
                  <div className={styles.robotUploadHistory}>
                    {(uploads.data?.items ?? []).slice(0, 5).map((upload) => (
                      <div key={upload.upload_id}>
                        <span>
                          <code>{upload.target.collection_task_id}</code>
                          <small>
                            {upload.source_format} ·{" "}
                            {shortDate(upload.created_at)}
                          </small>
                        </span>
                        <Tag
                          color={
                            upload.state === "COMMITTED" ? "green" : "blue"
                          }
                        >
                          {upload.state}
                        </Tag>
                      </div>
                    ))}
                    {!uploads.isPending &&
                    (uploads.data?.items.length ?? 0) === 0 ? (
                      <Typography.Text type="secondary">
                        暂无上传
                      </Typography.Text>
                    ) : null}
                  </div>
                </section>
              </div>

              {rejectedAttempts.length > 0 ? (
                <Alert
                  type="warning"
                  showIcon
                  title={`最近有 ${rejectedAttempts.length} 次失败尝试`}
                  description={rejectedAttempts
                    .slice(0, 3)
                    .map(
                      (attempt) =>
                        `${attempt.failure_stage}: ${attempt.failure_code ?? "UNKNOWN"}`,
                    )
                    .join("；")}
                />
              ) : null}
            </div>
          ) : null}
        </div>
      )}

      {activeError ? (
        <Alert type="error" showIcon title={errorMessage(activeError)} />
      ) : null}

      <Modal
        open={createOpen}
        title="新建机器人上传身份"
        footer={null}
        destroyOnHidden
        mask={{ closable: false }}
        onCancel={() => {
          if (createIdentity.isPending) return;
          setCreateOpen(false);
          createIdentity.reset();
        }}
      >
        <form
          className={styles.robotIdentityForm}
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            const displayName = String(data.get("displayName") ?? "").trim();
            if (!createRobotId || createFormats.length === 0) return;
            createIdentity.mutate(
              {
                scope,
                robotId: createRobotId,
                displayName: displayName || null,
                allowedFormats: createFormats,
                allowedTransports: ["HTTPS"],
              },
              {
                onSuccess: (envelope) => {
                  setSelectedIdentityId(envelope.data.ingest_identity_id);
                  setCreateOpen(false);
                  setCredentialMode("issue");
                },
              },
            );
          }}
        >
          <Alert
            type="info"
            showIcon
            title="身份仅绑定组织与 robot_id"
            description="项目归属不会写入身份；上传时由 collection_task_id 决定。"
          />
          <Form.Item label="Robot ID" required>
            <Select
              showSearch
              value={createRobotId}
              loading={organizationRobots.isPending}
              placeholder="选择组织中的 ACTIVE 机器人"
              optionFilterProp="label"
              options={availableRobots.map((robot) => ({
                value: robot.id,
                label: `${robot.displayName} · ${robot.id}`,
              }))}
              notFoundContent="没有尚未绑定身份的 ACTIVE 机器人"
              onChange={setCreateRobotId}
            />
          </Form.Item>
          <Form.Item label="显示名称">
            <Input name="displayName" autoComplete="off" maxLength={200} />
          </Form.Item>
          <Form.Item label="允许格式" required>
            <Select
              mode="tags"
              value={createFormats}
              options={formatOptions}
              onChange={setCreateFormats}
              tokenSeparators={[","]}
            />
          </Form.Item>
          <Flex justify="end" gap="small">
            <Button
              disabled={createIdentity.isPending}
              onClick={() => setCreateOpen(false)}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={createIdentity.isPending}
            >
              创建并签发凭据
            </Button>
          </Flex>
        </form>
      </Modal>

      <Modal
        open={policyDraft !== null}
        title="格式与上传策略"
        footer={null}
        destroyOnHidden
        mask={{ closable: false }}
        onCancel={() => {
          if (!updateIdentity.isPending) setPolicyDraft(null);
        }}
      >
        {selectedIdentity && policyDraft ? (
          <form
            className={styles.robotIdentityForm}
            onSubmit={(event) => {
              event.preventDefault();
              if (policyDraft.allowedFormats.length === 0) return;
              updateIdentity.mutate(
                {
                  scope,
                  identityId: selectedIdentity.ingest_identity_id,
                  allowedFormats: policyDraft.allowedFormats,
                  allowedTransports: selectedIdentity.allowed_transports,
                  uploadPolicy: policyFrom(selectedIdentity, policyDraft),
                },
                { onSuccess: () => setPolicyDraft(null) },
              );
            }}
          >
            <Form.Item label="允许格式" required>
              <Select
                mode="tags"
                value={policyDraft.allowedFormats}
                options={formatOptions}
                tokenSeparators={[","]}
                onChange={(allowedFormats) =>
                  setPolicyDraft((current) =>
                    current ? { ...current, allowedFormats } : current,
                  )
                }
              />
            </Form.Item>
            <div className={styles.robotPolicyGrid}>
              <Form.Item label="单资产上限（GiB）" required>
                <InputNumber
                  min={0.001}
                  step={1}
                  value={policyDraft.maxAssetGiB}
                  onChange={(value) =>
                    setPolicyDraft((current) =>
                      current && value
                        ? { ...current, maxAssetGiB: value }
                        : current,
                    )
                  }
                />
              </Form.Item>
              <Form.Item label="单批上限（GiB）" required>
                <InputNumber
                  min={0.001}
                  step={1}
                  value={policyDraft.maxBatchGiB}
                  onChange={(value) =>
                    setPolicyDraft((current) =>
                      current && value
                        ? { ...current, maxBatchGiB: value }
                        : current,
                    )
                  }
                />
              </Form.Item>
              <Form.Item label="最多资产数" required>
                <InputNumber
                  min={1}
                  max={4096}
                  value={policyDraft.maxAssets}
                  onChange={(value) =>
                    setPolicyDraft((current) =>
                      current && value
                        ? { ...current, maxAssets: value }
                        : current,
                    )
                  }
                />
              </Form.Item>
              <Form.Item label="分片授权有效期（分钟）" required>
                <InputNumber
                  min={1}
                  max={1440}
                  value={policyDraft.partAuthorizationMinutes}
                  onChange={(value) =>
                    setPolicyDraft((current) =>
                      current && value
                        ? { ...current, partAuthorizationMinutes: value }
                        : current,
                    )
                  }
                />
              </Form.Item>
              <Form.Item label="会话保留（小时）" required>
                <InputNumber
                  min={1}
                  max={8760}
                  value={policyDraft.retentionHours}
                  onChange={(value) =>
                    setPolicyDraft((current) =>
                      current && value
                        ? { ...current, retentionHours: value }
                        : current,
                    )
                  }
                />
              </Form.Item>
            </div>
            <Flex gap="large" wrap="wrap">
              <Checkbox
                checked={policyDraft.requireSha256}
                onChange={(event) =>
                  setPolicyDraft((current) =>
                    current
                      ? { ...current, requireSha256: event.target.checked }
                      : current,
                  )
                }
              >
                必须校验 SHA-256
              </Checkbox>
              <Checkbox
                checked={policyDraft.requireCrc64}
                onChange={(event) =>
                  setPolicyDraft((current) =>
                    current
                      ? { ...current, requireCrc64: event.target.checked }
                      : current,
                  )
                }
              >
                必须校验 CRC64
              </Checkbox>
            </Flex>
            <Flex justify="end" gap="small">
              <Button
                disabled={updateIdentity.isPending}
                onClick={() => setPolicyDraft(null)}
              >
                取消
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                loading={updateIdentity.isPending}
              >
                保存策略
              </Button>
            </Flex>
          </form>
        ) : null}
      </Modal>

      <Modal
        open={credentialMode !== null && issuedCredential === null}
        title={credentialMode === "rotate" ? "轮换平台凭据" : "签发平台凭据"}
        okText={credentialMode === "rotate" ? "轮换并撤销旧凭据" : "签发凭据"}
        cancelText="取消"
        confirmLoading={issueCredential.isPending}
        mask={{ closable: false }}
        onCancel={() => {
          if (issueCredential.isPending) return;
          setCredentialMode(null);
          setCredentialExpiry("");
          issueCredential.reset();
        }}
        onOk={() => {
          if (!selectedIdentity || !credentialMode) return;
          const expiresAt = credentialExpiry
            ? new Date(credentialExpiry).toISOString()
            : null;
          issueCredential.mutate(
            {
              scope,
              identityId: selectedIdentity.ingest_identity_id,
              rotate: credentialMode === "rotate",
              expiresAt,
            },
            {
              onSuccess: ({ credential }) => {
                setIssuedCredential(credential);
                setCopied(false);
              },
            },
          );
        }}
      >
        <Alert
          type={credentialMode === "rotate" ? "warning" : "info"}
          showIcon
          title={
            credentialMode === "rotate"
              ? "旧的活动凭据会立即撤销"
              : "凭据明文只在签发成功后显示一次"
          }
        />
        <Form.Item
          label="到期时间（可选）"
          className={styles.credentialExpiryField}
        >
          <Input
            type="datetime-local"
            value={credentialExpiry}
            onChange={(event) => setCredentialExpiry(event.target.value)}
          />
        </Form.Item>
      </Modal>

      <Modal
        open={issuedCredential !== null}
        title="立即保存机器人凭据"
        footer={null}
        destroyOnHidden
        mask={{ closable: false }}
        closable={false}
        keyboard={false}
      >
        {issuedCredential ? (
          <div className={styles.credentialReveal}>
            <Alert
              type="warning"
              showIcon
              title="这是唯一一次明文展示"
              description="关闭后平台不会再次返回该 Token；请写入机器人安全存储。"
            />
            <Input.TextArea
              value={issuedCredential.token}
              readOnly
              autoSize={{ minRows: 3, maxRows: 5 }}
              data-sensitive="credential"
              aria-label="机器人平台凭据"
            />
            <Flex justify="space-between" gap="small" wrap="wrap">
              <Button
                icon={
                  copied ? (
                    <Check size={16} aria-hidden="true" />
                  ) : (
                    <Copy size={16} aria-hidden="true" />
                  )
                }
                onClick={() => {
                  void navigator.clipboard
                    .writeText(issuedCredential.token)
                    .then(() => setCopied(true));
                }}
              >
                {copied ? "已复制" : "复制 Token"}
              </Button>
              <Button
                type="primary"
                onClick={() => {
                  setIssuedCredential(null);
                  setCredentialMode(null);
                  setCredentialExpiry("");
                  setCopied(false);
                  issueCredential.reset();
                }}
              >
                我已安全保存
              </Button>
            </Flex>
          </div>
        ) : null}
      </Modal>

      <Modal
        open={stateConfirmation}
        title={
          selectedIdentity?.state === "ENABLED"
            ? "确认停用身份"
            : "确认启用身份"
        }
        okText={selectedIdentity?.state === "ENABLED" ? "确认停用" : "确认启用"}
        okButtonProps={{ danger: selectedIdentity?.state === "ENABLED" }}
        confirmLoading={enableIdentity.isPending || disableIdentity.isPending}
        onCancel={() => setStateConfirmation(false)}
        onOk={() => {
          if (!selectedIdentity) return;
          const mutation =
            selectedIdentity.state === "ENABLED"
              ? disableIdentity
              : enableIdentity;
          mutation.mutate(
            { scope, identityId: selectedIdentity.ingest_identity_id },
            { onSuccess: () => setStateConfirmation(false) },
          );
        }}
      >
        <Alert
          type={selectedIdentity?.state === "ENABLED" ? "warning" : "info"}
          showIcon
          icon={
            selectedIdentity?.state === "ENABLED" ? (
              <CircleOff size={18} />
            ) : (
              <ShieldCheck size={18} />
            )
          }
          title={
            selectedIdentity?.state === "ENABLED"
              ? "停用后所有该机器人凭据立即拒绝新请求"
              : "启用后仍需有效凭据和有效采集任务才能上传"
          }
        />
      </Modal>

      <Modal
        open={revokeCredentialId !== null}
        title="撤销平台凭据"
        okText="确认撤销"
        okButtonProps={{ danger: true }}
        confirmLoading={revokeCredential.isPending}
        onCancel={() => setRevokeCredentialId(null)}
        onOk={() => {
          if (!selectedIdentity || !revokeCredentialId) return;
          revokeCredential.mutate(
            {
              scope,
              identityId: selectedIdentity.ingest_identity_id,
              credentialId: revokeCredentialId,
            },
            { onSuccess: () => setRevokeCredentialId(null) },
          );
        }}
      >
        <Alert
          type="warning"
          showIcon
          icon={<RotateCcw size={18} />}
          title="撤销立即生效且不可恢复"
          description="历史认证、上传与 Raw 归属事实不会被删除。"
        />
      </Modal>
    </section>
  );
}
