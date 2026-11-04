import {
  Alert,
  Button,
  Form,
  Input,
  Select,
  Space,
  Steps,
  type AlertProps,
} from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useScopedRobotModels } from "../robot-models/api/hooks";
import { isDomainError } from "../../shared/api/domain-error";
import { CompareServerDraft } from "./CompareServerDraft";
import { captureApi, useCaptureMutation } from "./api";
import {
  documentSchema,
  type Sample,
  type SchemeDetail,
  type SchemeDocument,
} from "./contracts";
import { AdapterFields } from "./AdapterFields";
import { DefinitionFields } from "./DefinitionFields";
import { PostprocessingRules } from "./PostprocessingRules";
import { QualityFields } from "./QualityFields";
import { Differences, EvidenceView } from "./EvidenceView";
import {
  emptyDocument,
  normalizeAdapters,
  validateEditor,
} from "./editor-model";
import { captureError, dataSummary, structureDiff } from "./presentation";
import {
  clearDraft,
  draftKey,
  readDraft,
  saveDraft,
  schemeLibraryPath,
} from "./navigation";
import { SamplePicker } from "./SamplePicker";
import { SchemeSourcePicker } from "./SchemeSourcePicker";

export function SchemeEditor({
  organizationId,
  schemeId,
  detail: incoming,
  initial,
  onSaved,
  back,
}: {
  organizationId: string;
  schemeId: string;
  detail?: SchemeDetail;
  initial?: SchemeDocument;
  onSaved: (detail: SchemeDetail) => void;
  back?: string;
}) {
  const navigate = useNavigate();
  const [confirmed, setConfirmed] = useState<SchemeDetail>();
  const detail =
    confirmed &&
    (!incoming ||
      confirmed.revision > incoming.revision ||
      (confirmed.revision === incoming.revision &&
        confirmed.versions.length >= incoming.versions.length &&
        confirmed.validations.length > incoming.validations.length))
      ? confirmed
      : incoming;
  function accept(result: SchemeDetail) {
    setConfirmed(result);
    setEdited(undefined);
    setBaseRevision(result.revision);
    clearDraft(key);
    onSaved(result);
  }
  const key = draftKey(`scheme:${schemeId}`, { organizationId });
  const saved = readDraft<{
    revision: number;
    document?: SchemeDocument;
    step: number;
  }>(key);
  const [edited, setEdited] = useState<SchemeDocument | undefined>(() => {
    const restored = saved?.document;
    if (!restored) return detail ? undefined : (initial ?? emptyDocument());
    // Older local forms used zero to represent an empty input.
    return restored.output.frequency_hz === 0
      ? { ...restored, output: { ...restored.output, frequency_hz: null } }
      : restored;
  });
  const doc = edited ?? detail?.draft ?? initial ?? emptyDocument();
  const [step, setStep] = useState(saved?.step ?? 0);
  const [sample, setSample] = useState<Sample | null>(null);
  const models = useScopedRobotModels(organizationId);
  const [baseRevision, setBaseRevision] = useState(
    (saved?.document ? saved.revision : detail?.revision) ?? 0,
  );
  const dirty = !detail || JSON.stringify(doc) !== JSON.stringify(detail.draft);
  const currentRevision = !dirty && detail ? detail.revision : baseRevision;
  const stale = !!detail && currentRevision !== detail.revision;
  const change = (next: SchemeDocument) => {
    if (next.input_mode === "AUTO") next = normalizeAdapters(next);
    const matchesServer =
      !!detail && JSON.stringify(next) === JSON.stringify(detail.draft);
    setEdited(matchesServer ? undefined : next);
    setBaseRevision(currentRevision);
    if (action.error && !revisionConflict) action.reset();
    saveDraft(key, {
      document: matchesServer ? undefined : next,
      revision: currentRevision,
      step,
    });
  };
  const goToStep = (value: number) => {
    setStep(value);
    if (dirty)
      saveDraft(key, { document: doc, revision: currentRevision, step: value });
    else saveDraft(key, { step: value, revision: currentRevision });
  };
  const action = useCaptureMutation(
    organizationId,
    async (kind: "save" | "validate" | "publish") => {
      if (kind === "save") {
        const normalized = normalizeAdapters({
          ...doc,
          output: {
            ...doc.output,
            algorithm:
              doc.output.algorithm === "capture-structure/v3"
                ? "capture-structure/v3"
                : "capture-structure/v2",
          },
        });
        const errors = validateEditor(normalized);
        if (errors.length) throw new Error(errors.join("；"));
        const result = await captureApi.save(organizationId, schemeId, {
          ...documentSchema.parse(normalized),
          expected_revision: currentRevision,
        });
        accept(result);
        navigate(
          schemeLibraryPath(back, schemeId) + (back ? "&" : "?") + "edit=1",
          { replace: true },
        );
        return;
      }
      if (!detail || dirty) throw new Error("请先保存方案；修改后需要重新验证");
      if (kind === "validate") {
        if (!sample?.source_path) throw new Error("请选择完整验证样本");
        const result = await captureApi.validate(
          organizationId,
          schemeId,
          currentRevision,
          sample,
        );
        accept(result);
      } else {
        await captureApi.publish(organizationId, schemeId, currentRevision);
        clearDraft(key);
        onSaved(await captureApi.detail(organizationId, schemeId));
        navigate(schemeLibraryPath(back, schemeId), { replace: true });
      }
    },
  );
  const latest = detail?.versions[0];
  const allValidated =
    !dirty &&
    (doc.input_mode === "AUTO"
      ? doc.adapters.some.bind(doc.adapters)
      : doc.adapters.every.bind(doc.adapters))((a) =>
      detail?.validations.some(
        (v) =>
          v.source_format === a.source_format &&
          v.status === "PASSED" &&
          v.revision === detail.revision,
      ),
    );
  const revisionConflict =
    isDomainError(action.error) &&
    (action.error.problemCode === "CAPTURE_REVISION_CONFLICT" ||
      action.error.code === "VERSION_CONFLICT");
  const conflict = stale || revisionConflict;
  const uncertain =
    action.variables === "save" &&
    isDomainError(action.error) &&
    ["NETWORK_ERROR", "SERVER_ERROR", "CONTRACT_MISMATCH"].includes(
      action.error.code,
    );
  const operation =
    action.variables === "validate"
      ? "验证"
      : action.variables === "publish"
        ? "发布"
        : "保存";
  const status = ((): {
    type: AlertProps["type"];
    title: string;
    reason?: string;
  } => {
    if (action.isPending)
      return { type: "info", title: "正在" + operation + "…" };
    if (conflict)
      return {
        type: "warning",
        title:
          (revisionConflict && operation !== "保存"
            ? operation + "未完成"
            : "本页修改未保存") + "：服务器草稿已更新",
      };
    if (uncertain)
      return { type: "warning", title: "保存结果未确认，请核对服务器草稿" };
    if (action.error)
      return {
        type: "error",
        title: operation + "失败",
        reason: captureError(action.error),
      };
    if (dirty) return { type: "info", title: "草稿未保存" };
    if (latest && latest.draft_revision === detail?.revision)
      return { type: "success", title: "已保存 · 已发布 v" + latest.version };
    if (allValidated)
      return { type: "success", title: "草稿已保存 · 验证通过，可发布" };
    if (detail?.validations.some((v) => v.status === "FAILED"))
      return { type: "warning", title: "草稿已保存 · 验证未通过" };
    return { type: "success", title: "草稿已保存 · 待验证" };
  })();
  return (
    <Form
      layout="vertical"
      disabled={action.isPending}
      style={{ maxWidth: 1000 }}
    >
      <Steps
        current={step}
        onChange={(value) => {
          if (!action.isPending) goToStep(value);
        }}
        items={[
          "适用范围",
          "数据定义",
          doc.input_mode === "AUTO" ? "自动识别上传" : "原始文件配置",
          "确认与保存",
        ].map((title) => ({ title }))}
      />
      <div style={{ marginTop: 24 }}>
        <Alert
          showIcon
          type={status.type}
          title={<span role="status">{status.title}</span>}
          description={status.reason}
          action={
            (conflict || uncertain) && !action.isPending ? (
              <CompareServerDraft
                organizationId={organizationId}
                schemeId={schemeId}
                current={doc}
                onLoad={(result) => {
                  accept(result);
                  setSample(null);
                  action.reset();
                }}
              />
            ) : undefined
          }
        />
        {step === 0 && (
          <>
            <Form.Item label="方案名称" required>
              <Input
                aria-label="方案名称"
                maxLength={120}
                value={doc.name}
                onChange={(e) => change({ ...doc, name: e.target.value })}
              />
            </Form.Item>
            <Form.Item label="说明">
              <Input.TextArea
                aria-label="方案说明"
                value={doc.description}
                onChange={(e) =>
                  change({ ...doc, description: e.target.value })
                }
              />
            </Form.Item>
            <Form.Item label="适用机器人型号（空表示不限制型号，仍需验证数据能力）">
              <Select
                aria-label="适用机器人型号"
                mode="multiple"
                value={doc.model_version_ids}
                loading={models.isFetching}
                options={(models.data?.items ?? [])
                  .filter((m) => m.currentPublishedVersionId)
                  .map((m) => ({
                    value: m.currentPublishedVersionId!,
                    label: m.displayName,
                  }))}
                onChange={(model_version_ids) =>
                  change({ ...doc, model_version_ids })
                }
              />
            </Form.Item>
          </>
        )}
        {step === 1 && (
          <>
            {!detail && (
              <SchemeSourcePicker
                organizationId={organizationId}
                onLoad={(document) => {
                  change({
                    ...doc,
                    output: document.output,
                    adapters: document.adapters,
                    quality_policy: document.quality_policy,
                  });
                  setSample(null);
                }}
              />
            )}
            <DefinitionFields document={doc} onChange={change} />
            <PostprocessingRules document={doc} onChange={change} />
            <QualityFields document={doc} onChange={change} />
          </>
        )}
        {step === 2 && (
          <>
            {doc.input_mode !== "AUTO" && (
              <p>
                设置方案如何读取设备上传的原始文件：选择文件格式，并在字段名称不同时设置对应关系。
              </p>
            )}
            <AdapterFields document={doc} onChange={change} />
          </>
        )}
        {step === 3 && (
          <>
            <h3>确认方案配置</h3>
            <p>方案名称：{doc.name || "未填写"}</p>
            <p>{dataSummary(doc)}</p>
            <p>
              原始文件：
              {doc.adapters
                .map((a) =>
                  a.source_format === "LEROBOT_V3"
                    ? "LeRobot v3"
                    : a.source_format === "CAPTURE_BUNDLE"
                      ? "MCAP＋独立 MP4"
                      : "MCAP",
                )
                .join("、") || "未选择"}
            </p>
            <Button
              type="primary"
              loading={action.isPending && action.variables === "save"}
              disabled={!dirty || conflict || action.isPending}
              onClick={() => action.mutate("save")}
            >
              保存方案
            </Button>
            <h3>验证与发布</h3>
            <p>
              {doc.input_mode === "AUTO"
                ? "任选一种输入格式的样本进行验证。"
                : "每种输入格式各验证一个样本。"}
            </p>
            <SamplePicker
              formats={doc.adapters.map((a) => a.source_format)}
              value={sample}
              onChange={setSample}
            />
            <Button
              loading={action.isPending && action.variables === "validate"}
              disabled={
                dirty || conflict || !sample?.source_path || action.isPending
              }
              onClick={() => action.mutate("validate")}
            >
              {doc.input_mode === "AUTO" ? "验证采集方案" : "验证当前格式"}
            </Button>
            {!dirty && <EvidenceView items={detail?.validations ?? []} />}
            {latest && (
              <details>
                <summary>与已发布 v{latest.version} 的差异</summary>
                <Differences
                  items={structureDiff(documentSchema.parse(latest), doc)}
                />
              </details>
            )}
            <Button
              type="primary"
              disabled={!allValidated || conflict || action.isPending}
              onClick={() => action.mutate("publish")}
            >
              发布新版本
            </Button>
          </>
        )}
        <Space style={{ marginTop: 24 }}>
          <Button
            disabled={step === 0 || action.isPending}
            onClick={() => goToStep(step - 1)}
          >
            上一步
          </Button>
          {step < 3 && (
            <Button
              type="primary"
              disabled={action.isPending}
              onClick={() => goToStep(step + 1)}
            >
              {step === 2 ? "下一步：确认与保存" : "下一步"}
            </Button>
          )}
        </Space>
      </div>
    </Form>
  );
}
