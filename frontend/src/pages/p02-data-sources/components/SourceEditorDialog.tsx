import {
  Alert,
  Button,
  Flex,
  Form,
  Input,
  Modal,
  Steps,
  type InputRef,
} from "antd";
import { useEffect, useRef } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";
import type {
  KnownConnectorConfiguration,
  WritableConnectorBinding,
  WritableConnectorConfiguration,
} from "../../../features/ingest/connectors/registry";
import { useRobotModels } from "../../../features/robot-models/api";
import { useRobots } from "../../../features/robots/api";
import { createZodResolver, RHFInput, RHFSelect } from "../../../shared/ui";
import styles from "../styles.module.css";

export type RobotProvisioningPhase = "idle" | "instance" | "model" | "source";
export type SourceEditorKind = "ROBOT" | "EDGE_AGENT" | "OSS_IMPORT";

const ROBOT_MULTI_SOURCE_FORMAT = "MULTI_FORMAT";

export type RobotSourceProvisioningDraft =
  | {
      readonly mode: "CREATE";
      readonly displayName: string;
      readonly serialNo: string;
      readonly modelVersionId: string;
    }
  | {
      readonly mode: "EXISTING";
      readonly robotId: string;
      readonly modelVersionId: string | null;
    };

export interface SourceDraft {
  readonly name: string;
  readonly sourceFormat: string;
  readonly sourceFormatVersion: string | null;
  readonly uploadPolicyCode: string;
  readonly configuration: WritableConnectorConfiguration;
  readonly binding: WritableConnectorBinding;
  readonly robotProvisioning?: RobotSourceProvisioningDraft;
  readonly credentialToken?: string;
  readonly changeReason?: string;
}

export interface SourceEditorInitialValue {
  readonly name: string;
  readonly sourceFormat: string;
  readonly sourceFormatVersion: string | null;
  readonly uploadPolicyCode: string;
  readonly configuration: KnownConnectorConfiguration;
  readonly binding: WritableConnectorBinding;
}

const sourceEditorSchema = z
  .object({
    mode: z.enum(["create", "update"]),
    name: z
      .string()
      .trim()
      .min(1, "请输入名称")
      .max(128, "名称最多 128 个字符"),
    kind: z.enum(["ROBOT", "EDGE_AGENT", "OSS_IMPORT"]),
    sourceFormat: z.string().trim().min(1, "请输入来源格式"),
    sourceFormatVersion: z.string().trim(),
    uploadPolicyCode: z.string().trim().min(1, "请输入上传策略"),
    robotProvisioningMode: z.enum(["CREATE", "EXISTING"]),
    robotId: z.string().trim(),
    robotDisplayName: z.string().trim().max(256, "实例名称最多 256 个字符"),
    robotSerialNo: z.string().trim().max(128, "序列号最多 128 个字符"),
    robotModelVersionId: z.string().trim(),
    transport: z.enum(["HTTPS", "MQTTS", "OUTBOUND_HTTPS"]),
    agentId: z.string().trim(),
    heartbeatPolicyId: z.string().trim(),
    sourceAlias: z.string().trim(),
    ossAccountAlias: z.string().trim(),
    bucketAlias: z.string().trim(),
    prefixHint: z.string().trim(),
    roleRef: z.string().trim(),
    sourceRegionCode: z.string().trim(),
    changeReason: z.string().trim().max(500, "变更原因最多 500 个字符"),
  })
  .superRefine((value, context) => {
    const requireField = (field: keyof typeof value, message: string) => {
      if (String(value[field]).length === 0) {
        context.addIssue({ code: "custom", path: [field], message });
      }
    };
    if (value.kind === "ROBOT") {
      if (
        value.mode === "update" ||
        value.robotProvisioningMode === "EXISTING"
      ) {
        requireField("robotId", "请选择机器人实例");
      } else {
        requireField("robotDisplayName", "请输入机器人实例名称");
        requireField("robotSerialNo", "请输入真实机器人的序列号");
        requireField("robotModelVersionId", "请选择已发布机器人模型");
      }
    } else if (value.kind === "EDGE_AGENT") {
      requireField("agentId", "请输入 Agent 稳定 ID");
      requireField("heartbeatPolicyId", "请输入心跳策略 ID");
    } else {
      requireField("sourceAlias", "请输入来源别名");
      requireField("ossAccountAlias", "请输入 OSS 账户别名");
      requireField("bucketAlias", "请输入 Bucket 别名");
      requireField("prefixHint", "请输入 Prefix 安全提示");
      requireField("roleRef", "请输入角色引用");
      requireField("sourceRegionCode", "请输入来源 Region");
    }
    if (value.mode === "update") requireField("changeReason", "请输入变更原因");
  });

type SourceFormValues = z.infer<typeof sourceEditorSchema>;

function defaults(
  mode: "create" | "update",
  initial?: SourceEditorInitialValue,
  canProvisionRobot = false,
  initialKind?: SourceEditorKind,
): SourceFormValues {
  const configuration = initial?.configuration;
  const binding = initial?.binding;
  const kind = configuration?.kind ?? initialKind ?? "ROBOT";
  return {
    mode,
    name: initial?.name ?? "",
    kind,
    sourceFormat:
      initial?.sourceFormat ??
      (kind === "ROBOT" ? ROBOT_MULTI_SOURCE_FORMAT : ""),
    sourceFormatVersion: initial?.sourceFormatVersion ?? "",
    uploadPolicyCode: initial?.uploadPolicyCode ?? "STANDARD",
    robotProvisioningMode:
      mode === "create" && canProvisionRobot ? "CREATE" : "EXISTING",
    robotId: binding?.kind === "ROBOT" ? binding.robotId : "",
    robotDisplayName: "",
    robotSerialNo: "",
    robotModelVersionId: "",
    transport:
      configuration?.kind === "ROBOT"
        ? configuration.transport
        : configuration?.kind === "EDGE_AGENT"
          ? configuration.transport
          : "HTTPS",
    agentId:
      binding?.kind === "EDGE_AGENT"
        ? binding.agentId
        : configuration?.kind === "EDGE_AGENT"
          ? configuration.agentId
          : "",
    heartbeatPolicyId:
      configuration?.kind === "EDGE_AGENT"
        ? configuration.heartbeatPolicyId
        : "",
    sourceAlias: binding?.kind === "OSS_IMPORT" ? binding.sourceAlias : "",
    ossAccountAlias:
      configuration?.kind === "OSS_IMPORT" ? configuration.ossAccountAlias : "",
    bucketAlias:
      configuration?.kind === "OSS_IMPORT" ? configuration.bucketAlias : "",
    prefixHint:
      configuration?.kind === "OSS_IMPORT" ? configuration.prefixHint : "",
    roleRef: configuration?.kind === "OSS_IMPORT" ? configuration.roleRef : "",
    sourceRegionCode:
      configuration?.kind === "OSS_IMPORT"
        ? configuration.sourceRegionCode
        : "",
    changeReason: "",
  };
}

function makeConfiguration(
  value: SourceFormValues,
): WritableConnectorConfiguration {
  if (value.kind === "ROBOT") {
    return {
      kind: value.kind,
      transport: value.transport === "MQTTS" ? "MQTTS" : "HTTPS",
    };
  }
  if (value.kind === "EDGE_AGENT") {
    return {
      kind: value.kind,
      agentId: value.agentId,
      transport: value.transport === "MQTTS" ? "MQTTS" : "OUTBOUND_HTTPS",
      heartbeatPolicyId: value.heartbeatPolicyId,
    };
  }
  return {
    kind: value.kind,
    ossAccountAlias: value.ossAccountAlias,
    bucketAlias: value.bucketAlias,
    prefixHint: value.prefixHint,
    roleRef: value.roleRef,
    sourceRegionCode: value.sourceRegionCode,
  };
}

function makeBinding(value: SourceFormValues): WritableConnectorBinding {
  if (value.kind === "ROBOT")
    return { kind: value.kind, robotId: value.robotId };
  if (value.kind === "EDGE_AGENT")
    return { kind: value.kind, agentId: value.agentId };
  return { kind: value.kind, sourceAlias: value.sourceAlias };
}

function ConnectorFields({
  control,
  kind,
  mode,
  pending,
  canProvisionRobot,
  robotProvisioningMode,
  robotOptions,
  robotModelOptions,
  robotsPending,
  robotModelsPending,
}: {
  readonly control: ReturnType<typeof useForm<SourceFormValues>>["control"];
  readonly kind: SourceFormValues["kind"];
  readonly mode: "create" | "update";
  readonly pending: boolean;
  readonly canProvisionRobot: boolean;
  readonly robotProvisioningMode: SourceFormValues["robotProvisioningMode"];
  readonly robotOptions: { readonly label: string; readonly value: string }[];
  readonly robotModelOptions: {
    readonly label: string;
    readonly value: string;
  }[];
  readonly robotsPending: boolean;
  readonly robotModelsPending: boolean;
}) {
  if (kind === "ROBOT") {
    return (
      <>
        {mode === "create" ? (
          <RHFSelect
            control={control}
            name="robotProvisioningMode"
            label="机器人实例"
            disabled={pending}
            description={
              canProvisionRobot
                ? "可以在创建数据源时同步创建机器人实例，也可以关联组织中已有的实例。"
                : "当前授权只能关联已有机器人实例。"
            }
            options={[
              ...(canProvisionRobot
                ? [{ label: "创建新实例", value: "CREATE" as const }]
                : []),
              { label: "选择已有实例", value: "EXISTING" as const },
            ]}
          />
        ) : null}
        {mode === "create" && robotProvisioningMode === "CREATE" ? (
          <>
            <RHFInput
              control={control}
              name="robotDisplayName"
              label="实例名称"
              disabled={pending}
              autoComplete="off"
              placeholder="例如：装配机器人 A"
            />
            <RHFInput
              control={control}
              name="robotSerialNo"
              label="真实机器人序列号"
              disabled={pending}
              autoComplete="off"
              description="用于保证同一组织内真实机器人唯一。"
            />
          </>
        ) : (
          <RHFSelect
            control={control}
            name="robotId"
            label="机器人实例"
            disabled={pending}
            loading={robotsPending}
            showSearch
            optionFilterProp="label"
            placeholder="请选择机器人实例"
            notFoundContent={
              robotsPending
                ? "正在加载机器人实例…"
                : "当前组织暂无可用机器人实例"
            }
            options={robotOptions}
          />
        )}
        {mode === "create" ? (
          <RHFSelect
            control={control}
            name="robotModelVersionId"
            label={
              robotProvisioningMode === "CREATE"
                ? "机器人模型"
                : "机器人模型（可选）"
            }
            disabled={pending}
            loading={robotModelsPending}
            showSearch
            optionFilterProp="label"
            allowClear={robotProvisioningMode === "EXISTING"}
            placeholder={
              robotProvisioningMode === "CREATE"
                ? "请选择已发布模型"
                : "不选择则保留实例当前模型"
            }
            description="这里只显示已经发布的模型版本。"
            notFoundContent={
              robotModelsPending
                ? "正在加载机器人模型…"
                : "当前组织暂无已发布机器人模型"
            }
            options={robotModelOptions}
          />
        ) : null}
        <RHFSelect
          control={control}
          name="transport"
          label="传输"
          disabled={pending}
          options={[
            { label: "HTTPS", value: "HTTPS" },
            { label: "MQTTS", value: "MQTTS" },
          ]}
        />
      </>
    );
  }
  if (kind === "EDGE_AGENT") {
    return (
      <>
        <RHFInput
          control={control}
          name="agentId"
          label="Agent 稳定 ID"
          disabled={pending}
          autoComplete="off"
        />
        <RHFSelect
          control={control}
          name="transport"
          label="传输"
          disabled={pending}
          options={[
            { label: "OUTBOUND_HTTPS", value: "OUTBOUND_HTTPS" },
            { label: "MQTTS", value: "MQTTS" },
          ]}
        />
        <RHFInput
          control={control}
          name="heartbeatPolicyId"
          label="心跳策略 ID"
          disabled={pending}
          autoComplete="off"
        />
      </>
    );
  }
  return (
    <>
      <RHFInput
        control={control}
        name="sourceAlias"
        label="来源别名"
        disabled={pending}
        autoComplete="off"
      />
      <RHFInput
        control={control}
        name="ossAccountAlias"
        label="OSS 账户别名"
        disabled={pending}
        autoComplete="off"
      />
      <RHFInput
        control={control}
        name="bucketAlias"
        label="Bucket 别名"
        disabled={pending}
        autoComplete="off"
      />
      <RHFInput
        control={control}
        name="prefixHint"
        label="Prefix 安全提示"
        disabled={pending}
        autoComplete="off"
      />
      <RHFInput
        control={control}
        name="roleRef"
        label="角色引用"
        disabled={pending}
        autoComplete="off"
      />
      <RHFInput
        control={control}
        name="sourceRegionCode"
        label="来源 Region"
        disabled={pending}
        autoComplete="off"
      />
    </>
  );
}

export function SourceEditorDialog(props: {
  readonly open: boolean;
  readonly mode: "create" | "update";
  readonly pending: boolean;
  readonly initial?: SourceEditorInitialValue;
  readonly initialKind?: SourceEditorKind;
  readonly canProvisionRobot?: boolean;
  readonly provisioningPhase?: RobotProvisioningPhase;
  readonly errorMessage?: string | null;
  readonly onClose: () => void;
  readonly onSubmit: (draft: SourceDraft) => void;
}) {
  const canProvisionRobot = props.canProvisionRobot ?? false;
  const credentialRef = useRef<InputRef>(null);
  const robots = useRobots();
  const robotModels = useRobotModels();
  const form = useForm<SourceFormValues>({
    defaultValues: defaults(
      props.mode,
      props.initial,
      canProvisionRobot,
      props.initialKind,
    ),
    mode: "onChange",
    resolver: createZodResolver(sourceEditorSchema),
  });
  const kind = form.watch("kind");
  const robotProvisioningMode = form.watch("robotProvisioningMode");
  const robotSerialNo = form.watch("robotSerialNo").trim();
  const existingRobot = (robots.data?.items ?? []).find(
    (robot) => robot.serialNo === robotSerialNo,
  );
  const robotOptions = (robots.data?.items ?? [])
    .filter(
      (robot) =>
        robot.lifecycle !== "DISABLED" && robot.lifecycle !== "RETIRED",
    )
    .map((robot) => ({
      value: robot.id,
      label: `${robot.displayName} · ${robot.serialNo} · ${robot.id}`,
    }));
  const robotModelOptions = (robotModels.data?.items ?? [])
    .filter((model) => model.currentPublishedVersionId !== null)
    .map((model) => ({
      value: model.currentPublishedVersionId!,
      label: `${model.displayName} · ${model.manufacturer}/${model.modelCode}`,
    }));

  useEffect(() => {
    if (!props.open) return;
    form.reset(
      defaults(props.mode, props.initial, canProvisionRobot, props.initialKind),
    );
    if (credentialRef.current?.input) credentialRef.current.input.value = "";
  }, [
    canProvisionRobot,
    form,
    props.initial,
    props.initialKind,
    props.mode,
    props.open,
  ]);

  const close = () => {
    if (props.pending) return;
    form.reset(
      defaults(props.mode, props.initial, canProvisionRobot, props.initialKind),
    );
    if (credentialRef.current?.input) credentialRef.current.input.value = "";
    props.onClose();
  };

  return (
    <Modal
      open={props.open}
      title={
        props.mode === "create"
          ? props.initialKind === "ROBOT"
            ? "新建机器人数据源"
            : "新建数据源"
          : "编辑数据源"
      }
      footer={null}
      onCancel={close}
      closable={!props.pending}
      keyboard={!props.pending}
      mask={{ closable: false }}
      destroyOnHidden
      width={720}
      zIndex={1100}
      afterOpenChange={(open) => {
        if (open) form.setFocus("name");
      }}
    >
      <form
        className={styles.editorForm}
        noValidate
        onSubmit={(event) => {
          void form.handleSubmit((value) => {
            if (
              value.kind === "ROBOT" &&
              props.mode === "create" &&
              value.robotProvisioningMode === "CREATE" &&
              existingRobot
            ) {
              form.setError("robotSerialNo", {
                type: "validate",
                message: `该序列号已对应实例“${existingRobot.displayName}”，请关联已有实例。`,
              });
              return;
            }
            const credentialToken = credentialRef.current?.input?.value ?? "";
            props.onSubmit({
              name: value.name,
              sourceFormat:
                value.kind === "ROBOT"
                  ? ROBOT_MULTI_SOURCE_FORMAT
                  : value.sourceFormat,
              sourceFormatVersion:
                value.kind === "ROBOT"
                  ? null
                  : value.sourceFormatVersion || null,
              uploadPolicyCode: value.uploadPolicyCode,
              configuration: makeConfiguration(value),
              binding: makeBinding(value),
              ...(value.kind === "ROBOT" && props.mode === "create"
                ? {
                    robotProvisioning:
                      value.robotProvisioningMode === "CREATE"
                        ? {
                            mode: "CREATE" as const,
                            displayName: value.robotDisplayName,
                            serialNo: value.robotSerialNo,
                            modelVersionId: value.robotModelVersionId,
                          }
                        : {
                            mode: "EXISTING" as const,
                            robotId: value.robotId,
                            modelVersionId: value.robotModelVersionId || null,
                          },
                  }
                : {}),
              ...(credentialToken ? { credentialToken } : {}),
              ...(value.changeReason
                ? { changeReason: value.changeReason }
                : {}),
            });
            if (credentialRef.current?.input)
              credentialRef.current.input.value = "";
          })(event);
        }}
      >
        {props.errorMessage ? (
          <Alert type="error" showIcon title={props.errorMessage} />
        ) : null}
        {props.mode === "create" && kind === "ROBOT" ? (
          <section
            className={styles.provisioningFlow}
            aria-label="机器人数据源创建流程"
          >
            <Alert
              type="info"
              showIcon
              title="实例、模型和数据源会按顺序关联"
              description="机器人实例不绑定数据格式；LeRobot、MCAP 等格式在每次上传时识别，并由机器人上传身份策略控制。创建完成后，再签发一次性凭据并配置到真实机器人。"
            />
            <Steps
              size="small"
              current={
                props.provisioningPhase === "model"
                  ? 1
                  : props.provisioningPhase === "source"
                    ? 2
                    : 0
              }
              status={
                props.errorMessage
                  ? "error"
                  : props.pending
                    ? "process"
                    : "wait"
              }
              items={[
                {
                  title:
                    robotProvisioningMode === "CREATE"
                      ? "创建实例"
                      : "读取实例",
                },
                { title: "绑定模型" },
                { title: "创建数据源" },
              ]}
            />
          </section>
        ) : null}
        {props.mode === "create" &&
        kind === "ROBOT" &&
        (robots.isError || robotModels.isError) ? (
          <Alert
            type="error"
            showIcon
            title="机器人实例或模型暂时无法加载"
            description="请恢复服务后重试；系统不会允许提交不可验证的机器人 ID 或模型版本。"
          />
        ) : null}
        <div className={styles.formGrid}>
          <RHFInput
            control={form.control}
            name="name"
            label="名称"
            disabled={props.pending}
            autoComplete="off"
          />
          <RHFSelect
            control={form.control}
            name="kind"
            label="连接器类型"
            disabled={props.pending || props.mode === "update"}
            preserveValueWhenDisabled={props.mode === "update"}
            options={[
              { label: "机器人", value: "ROBOT" },
              { label: "边缘代理", value: "EDGE_AGENT" },
              { label: "OSS 导入", value: "OSS_IMPORT" },
            ]}
          />
          {kind !== "ROBOT" ? (
            <>
              <RHFInput
                control={form.control}
                name="sourceFormat"
                label="来源格式"
                disabled={props.pending}
              />
              <RHFInput
                control={form.control}
                name="sourceFormatVersion"
                label="格式版本"
                disabled={props.pending}
              />
            </>
          ) : null}
          <RHFInput
            control={form.control}
            name="uploadPolicyCode"
            label="上传策略"
            disabled={props.pending}
          />
          <ConnectorFields
            control={form.control}
            kind={kind}
            mode={props.mode}
            pending={props.pending}
            canProvisionRobot={canProvisionRobot}
            robotProvisioningMode={robotProvisioningMode}
            robotOptions={robotOptions}
            robotModelOptions={robotModelOptions}
            robotsPending={robots.isPending}
            robotModelsPending={robotModels.isPending}
          />
          {kind === "ROBOT" &&
          props.mode === "create" &&
          robotProvisioningMode === "CREATE" &&
          existingRobot ? (
            <Alert
              className={styles.fullWidthField}
              type="warning"
              showIcon
              title={`序列号 ${existingRobot.serialNo} 已对应机器人实例“${existingRobot.displayName}”`}
              description="一个真实机器人只能有一个实例，请直接关联已有实例。"
              action={
                <Button
                  size="small"
                  onClick={() => {
                    form.setValue("robotProvisioningMode", "EXISTING", {
                      shouldDirty: true,
                      shouldValidate: true,
                    });
                    form.setValue("robotId", existingRobot.id, {
                      shouldDirty: true,
                      shouldValidate: true,
                    });
                    form.clearErrors("robotSerialNo");
                  }}
                >
                  关联此实例
                </Button>
              }
            />
          ) : null}
          {props.mode === "create" ? (
            <Form.Item
              className={styles.fullWidthField}
              label="访问 Token（可选）"
              help="仅当数据源需要 Token 认证时填写。Token 仅随本次请求提交，页面不会回显或保存到浏览器缓存。"
            >
              <Input.Password
                ref={credentialRef}
                name="credentialToken"
                aria-label="访问 Token（可选）"
                autoComplete="new-password"
                data-sensitive="credential"
                disabled={props.pending}
                visibilityToggle={false}
              />
            </Form.Item>
          ) : (
            <Controller
              control={form.control}
              name="changeReason"
              render={({ field, fieldState }) => (
                <Form.Item
                  className={styles.fullWidthField}
                  label="变更原因"
                  help={fieldState.error?.message}
                  validateStatus={fieldState.error ? "error" : undefined}
                >
                  <Input.TextArea
                    {...field}
                    aria-label="变更原因"
                    rows={3}
                    maxLength={500}
                    disabled={props.pending}
                    status={fieldState.error ? "error" : undefined}
                  />
                </Form.Item>
              )}
            />
          )}
        </div>
        <Flex justify="end" gap="small" wrap="wrap">
          <Button onClick={close} disabled={props.pending}>
            取消
          </Button>
          <Button type="primary" htmlType="submit" loading={props.pending}>
            {props.mode === "create" ? "创建" : "保存"}
          </Button>
        </Flex>
      </form>
    </Modal>
  );
}
