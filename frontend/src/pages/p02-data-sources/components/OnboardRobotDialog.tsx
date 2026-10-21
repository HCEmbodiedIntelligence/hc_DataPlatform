import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Steps,
} from "antd";
import { useEffect, useState } from "react";
import { useOrganizationCapabilities } from "../../../shared/auth/use-capabilities";
import { RobotSchemeAssociations } from "../../../features/capture-schemes/RobotSchemeAssociations";
import {
  draftKey,
  readDraft,
  saveDraft,
  clearDraft,
} from "../../../features/capture-schemes/navigation";
import type { IngestScope } from "../../../entities/data-source";
import { createMutationIntentKey } from "../../../features/ingest/mutation-machine";
import { useScopedRobots } from "../../../features/robots/api/hooks";
import { useScopedRobotModels } from "../../../features/robot-models/api/hooks";
import {
  useOnboardRobot,
  type OnboardCommand,
  type OnboardingRobot,
} from "../../../features/robot-ingest/onboarding";
import { ConnectionLink } from "../../../features/robot-ingest/ConnectionLink";

export function OnboardRobotDialog({
  scope,
  robot,
  canCreate,
  resumeRobotId,
  onClose,
  onDone,
}: {
  scope: IngestScope;
  robot?: OnboardingRobot;
  canCreate: boolean;
  resumeRobotId?: string;
  onClose: () => void;
  onDone: (robot: OnboardingRobot) => void;
}) {
  const storageKey = draftKey(
    `onboarding:${robot?.robot_id ?? resumeRobotId ?? "new"}`,
    scope,
  );
  const receiptKey = `${storageKey}:receipt`;
  const savedReceipt = readDraft<OnboardingRobot>(receiptKey);
  const capabilities = useOrganizationCapabilities(scope.organizationId);
  const [schemeReady, setSchemeReady] = useState(false);
  const [draft, setDraft] = useState<OnboardCommand>(() => {
    const saved = readDraft<OnboardCommand>(storageKey);
    if (saved) return saved;
    return {
      robot_id: robot?.robot_id ?? "",
      allowed_formats: ["MCAP", "CAPTURE_BUNDLE", "LEROBOT_V3"],
      idempotency_key: createMutationIntentKey(),
      generate_credential: false,
    };
  });
  const [mode, setMode] = useState(
    draft.robot_id || !canCreate ? "existing" : "new",
  );
  const [step, setStep] = useState(savedReceipt ? 2 : 0);
  const [savedRobot, setSavedRobot] = useState<OnboardingRobot | null>(
    savedReceipt,
  );
  const robots = useScopedRobots(scope.organizationId);
  const models = useScopedRobotModels(scope.organizationId);
  const operation = useOnboardRobot(scope);
  const change = (patch: Partial<OnboardCommand>) =>
    setDraft((current) => ({ ...current, ...patch }));
  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(draft));
    } catch {
      /* no secrets persisted */
    }
  }, [draft, storageKey]);
  const finish = (item: OnboardingRobot) => {
    try {
      sessionStorage.removeItem(storageKey);
      clearDraft(receiptKey);
    } catch {
      /* unavailable storage */
    }
    operation.reset();
    onDone(item);
  };
  const save = async () => {
    try {
      const result = await operation.mutateAsync({
        ...draft,
        generate_credential: false,
      });
      setSavedRobot(result.robot);
      saveDraft(receiptKey, result.robot);
      setStep(2);
    } catch {
      /* Keep draft and receipt for continuation after partial failure. */
    }
  };
  const validDevice =
    mode === "existing"
      ? Boolean(draft.robot_id)
      : Boolean(
          draft.display_name?.trim() &&
            draft.serial_no?.trim() &&
            draft.model_version_id,
        );
  return (
    <Modal
      open
      title="接入机器人"
      width={760}
      footer={null}
      mask={{ closable: false }}
      onCancel={() => {
        if (!operation.isPending) {
          operation.reset();
          onClose();
        }
      }}
    >
      <Steps
        current={step}
        size="small"
        items={["选择设备", "设置上传规则", "关联采集方案", "连接采集端"].map(
          (title) => ({
            title,
          }),
        )}
      />
      <Form layout="vertical" style={{ marginTop: 24 }}>
        {step === 0 && (
          <>
            <Form.Item label="选择或创建机器人">
              <Select
                aria-label="选择或创建机器人"
                value={mode}
                onChange={(value) => {
                  setMode(value);
                  change({ robot_id: "" });
                }}
                options={[
                  { value: "existing", label: "选择已有机器人" },
                  ...(canCreate
                    ? [{ value: "new", label: "创建新机器人" }]
                    : []),
                ]}
              />
            </Form.Item>
            {mode === "existing" ? (
              <Form.Item label="机器人">
                <Select
                  aria-label="机器人"
                  showSearch
                  optionFilterProp="label"
                  value={draft.robot_id || undefined}
                  loading={robots.isPending}
                  onChange={(robot_id) => change({ robot_id })}
                  options={(robots.data?.items ?? []).map((item) => ({
                    value: item.id,
                    label: `${item.displayName} · ${item.serialNo} · ${item.id}`,
                  }))}
                />
              </Form.Item>
            ) : (
              <>
                <Form.Item label="名称" required>
                  <Input
                    aria-label="名称"
                    maxLength={200}
                    value={draft.display_name ?? ""}
                    onChange={(e) => change({ display_name: e.target.value })}
                  />
                </Form.Item>
                <Form.Item label="真实序列号" required>
                  <Input
                    aria-label="真实序列号"
                    maxLength={128}
                    value={draft.serial_no ?? ""}
                    onChange={(e) =>
                      change({ serial_no: e.target.value.trim() })
                    }
                  />
                </Form.Item>
              </>
            )}
            <Form.Item
              label={
                mode === "new"
                  ? "已发布机器人模型"
                  : "补全模型（已有绑定时保留原模型）"
              }
              required={mode === "new"}
            >
              <Select
                aria-label="已发布机器人模型"
                allowClear
                showSearch
                optionFilterProp="label"
                value={draft.model_version_id}
                loading={models.isPending}
                onChange={(model_version_id) => change({ model_version_id })}
                options={(models.data?.items ?? [])
                  .filter((item) => item.currentPublishedVersionId)
                  .map((item) => ({
                    value: item.currentPublishedVersionId!,
                    label: item.displayName,
                  }))}
              />
            </Form.Item>
            {(robots.isError || models.isError) && (
              <Alert
                type="error"
                title="设备或模型读取失败"
                action={
                  <Button
                    onClick={() => {
                      void robots.refetch();
                      void models.refetch();
                    }}
                  >
                    重试
                  </Button>
                }
              />
            )}
            <Button
              type="primary"
              disabled={!validDevice}
              onClick={() => setStep(1)}
            >
              下一步
            </Button>
          </>
        )}
        {step === 1 && (
          <>
            <Form.Item label="允许上传格式" required>
              <Checkbox.Group
                value={draft.allowed_formats}
                onChange={(allowed_formats) =>
                  change({ allowed_formats: allowed_formats as string[] })
                }
                options={[
                  { label: "MCAP", value: "MCAP" },
                  { label: "连续采集包", value: "CAPTURE_BUNDLE" },
                  { label: "LeRobot v3", value: "LEROBOT_V3" },
                ]}
              />
            </Form.Item>
            <p>
              已有上传规则与停用状态会保留。文件大小等限制可在详情的“格式与策略”中调整。每次上传使用采集任务
              ID 确定项目、数据集和 Region。
            </p>
            <Space>
              <Button disabled={operation.isPending} onClick={() => setStep(0)}>
                上一步
              </Button>
              <Button
                type="primary"
                loading={operation.isPending}
                disabled={!draft.allowed_formats.length}
                onClick={() => void save()}
              >
                保存并继续
              </Button>
            </Space>
          </>
        )}
        {step === 2 && savedRobot && (
          <>
            <RobotSchemeAssociations
              key={savedRobot.robot_id}
              organizationId={scope.organizationId}
              robotId={savedRobot.robot_id}
              modelVersionId={savedRobot.model_version_id}
              allowedFormats={draft.allowed_formats}
              canManage={capabilities.has("robot.manage")}
              onReadyChange={setSchemeReady}
            />
            {!schemeReady && (
              <Alert
                type="info"
                title="可以先完成接入；方案关联或映射验证未完成时，采集方案待配置。"
              />
            )}
            <Button style={{ marginTop: 16 }} onClick={() => setStep(3)}>
              继续连接采集端
            </Button>
          </>
        )}
        {step === 3 && savedRobot?.identity && (
          <>
            <ConnectionLink
              scope={scope}
              identityId={savedRobot.identity.ingest_identity_id}
              disabled={savedRobot.upload_permission !== "ENABLED"}
            />
            <Alert
              type="info"
              title="等待设备连接"
              description="粘贴连接链接并连接后，平台会显示实际设备通信状态。"
            />
            <Button
              style={{ marginTop: 16 }}
              onClick={() => finish(savedRobot)}
            >
              查看接入详情
            </Button>
          </>
        )}
        {operation.isError && (
          <Alert
            style={{ marginTop: 16 }}
            type="error"
            showIcon
            title="接入尚未完成"
            description={`${operation.error.message} 已完成的设备与身份会保留，重试继续使用原记录。`}
          />
        )}
      </Form>
    </Modal>
  );
}
