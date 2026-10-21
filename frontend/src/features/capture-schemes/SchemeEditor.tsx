import { Alert, Button, Form, Input, Select, Space, Steps } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useScopedRobotModels } from "../robot-models/api/hooks";
import { captureApi, useCaptureMutation } from "./api";
import {
  documentSchema,
  type Sample,
  type SchemeDetail,
  type SchemeDocument,
} from "./contracts";
import { AdapterFields } from "./AdapterFields";
import { DefinitionFields } from "./DefinitionFields";
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
  detail,
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
  const key = draftKey(`scheme:${schemeId}`, { organizationId });
  const saved = readDraft<{
    revision: number;
    document: SchemeDocument;
    step: number;
  }>(key);
  const [doc, setDoc] = useState<SchemeDocument>(() => {
    const restored =
      saved?.document ?? initial ?? detail?.draft ?? emptyDocument();
    // Older local forms used zero to represent an empty input.
    return saved && restored.output.frequency_hz === 0
      ? { ...restored, output: { ...restored.output, frequency_hz: null } }
      : restored;
  });
  const [step, setStep] = useState(saved?.step ?? 0);
  const [sample, setSample] = useState<Sample | null>(null);
  const [localError, setLocalError] = useState("");
  const models = useScopedRobotModels(organizationId);
  const [baseRevision, setBaseRevision] = useState(
    saved?.revision ?? detail?.revision ?? 0,
  );
  const currentRevision = baseRevision;
  const dirty = !detail || JSON.stringify(doc) !== JSON.stringify(detail.draft);
  const change = (next: SchemeDocument) => {
    setDoc(next);
    setLocalError("");
    saveDraft(key, { document: next, revision: currentRevision, step });
  };
  const goToStep = (value: number) => {
    setStep(value);
    saveDraft(key, { document: doc, revision: currentRevision, step: value });
  };
  const action = useCaptureMutation(
    organizationId,
    async (kind: "save" | "validate" | "publish") => {
      if (kind === "save") {
        const normalized = normalizeAdapters(doc);
        const errors = validateEditor(normalized);
        if (errors.length) throw new Error(errors.join("；"));
        const result = await captureApi.save(organizationId, schemeId, {
          ...documentSchema.parse(normalized),
          expected_revision: currentRevision,
        });
        setDoc(result.draft);
        setBaseRevision(result.revision);
        onSaved(result);
        clearDraft(key);
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
        setDoc(result.draft);
        setBaseRevision(result.revision);
        clearDraft(key);
        onSaved(result);
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
    doc.adapters.every((a) =>
      detail?.validations.some(
        (v) =>
          v.source_format === a.source_format &&
          v.status === "PASSED" &&
          v.revision === detail.revision,
      ),
    );
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
        items={["适用范围", "数据定义", "原始文件配置", "确认与保存"].map(
          (title) => ({ title }),
        )}
      />
      <div style={{ marginTop: 24 }}>
        {detail && baseRevision !== detail.revision && (
          <Alert
            type="warning"
            title="方案已被更新，当前输入已保留，请核对差异后再保存。"
            description={
              <details>
                <summary>当前输入与已保存方案的差异</summary>
                <Differences items={structureDiff(detail.draft, doc)} />
              </details>
            }
          />
        )}
        {latest && (
          <Alert
            type="info"
            title={`保存后需验证并发布才会生效。已有任务继续使用已发布的 v${latest.version}。`}
          />
        )}
        {dirty && detail?.validations.length ? (
          <Alert
            type="warning"
            title="方案已修改，保存后请重新验证每种输入格式。"
          />
        ) : null}
        {(localError || action.error) && (
          <Alert
            type="error"
            title={localError || captureError(action.error)}
          />
        )}
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
          </>
        )}
        {step === 2 && (
          <>
            <p>
              设置方案如何读取设备上传的原始文件：选择文件格式，并在字段名称不同时设置对应关系。
            </p>
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
              loading={action.isPending}
              disabled={!dirty || action.isPending}
              onClick={() => action.mutate("save")}
            >
              保存方案
            </Button>
            {!dirty && <p>方案已保存。验证通过并发布后，即可用于采集任务。</p>}
            <h3>验证与发布</h3>
            <p>
              保存完整配置后，为每种原始文件格式选择真实样本验证。未填写的帧率会在验证通过后自动读取；也可以保存后稍后再验证。
            </p>
            <SamplePicker
              formats={doc.adapters.map((a) => a.source_format)}
              value={sample}
              onChange={setSample}
            />
            <Button
              disabled={dirty || !sample?.source_path || action.isPending}
              onClick={() => action.mutate("validate")}
            >
              验证当前格式
            </Button>
            {!dirty && <EvidenceView items={detail?.validations ?? []} />}
            {latest && (
              <>
                <h3>与最新发布版本的明确差异</h3>
                <Differences
                  items={structureDiff(documentSchema.parse(latest), doc)}
                />
              </>
            )}
            <Button
              type="primary"
              disabled={!allValidated || action.isPending}
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
