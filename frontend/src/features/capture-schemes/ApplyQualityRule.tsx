import { Alert, Button, Checkbox, Input, Modal, Select, Space } from "antd";
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { qualityFindingText } from "../../shared/lib/quality-presentation";
import { type EngineRule, useQualityRules } from "./api";
import type { SchemeDocument } from "./contracts";
import { captureError } from "./presentation";
import { QualityFields } from "./QualityFields";
import { qualityPolicySchema } from "./quality-contract";
import styles from "./postprocessing.module.css";

type Policy = SchemeDocument["quality_policy"];

export function ApplyQualityRule({
  org,
  document,
  onChange,
}: {
  org: string;
  document: SchemeDocument;
  onChange: (next: SchemeDocument) => void;
}) {
  const [draft, setDraft] = useState<Policy>();
  const rules = useQualityRules(org, draft !== undefined);
  const [current, setCurrent] = useState<EngineRule["code"]>();
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const library = rules.data;
  const engines = library?.engine_rules ?? [];
  const enabled = draft?.enabled_codes ?? engines.map((item) => item.code);
  const selected = document.quality_policy.enabled_codes;
  const term = query.trim().toLowerCase();
  const visible = engines.filter((item) =>
    [
      item.code,
      qualityFindingText(item.code).title,
      ...(library?.custom_rules
        .filter((template) => template.code === item.code)
        .map((template) => template.name) ?? []),
    ].some((value) => value.toLowerCase().includes(term)),
  );
  const rule = visible.find((item) => item.code === current) ?? visible[0];
  const templates =
    library?.custom_rules.filter((template) => template.code === rule?.code) ??
    [];

  function change(patch: Partial<Policy>) {
    setDraft((prev) => prev && { ...prev, ...patch });
    setError("");
  }
  function toggle(code: EngineRule["code"], checked: boolean) {
    return engines
      .filter((item) =>
        item.code === code ? checked : enabled.includes(item.code),
      )
      .map((item) => item.code);
  }
  function apply() {
    if (!draft || !library) return;
    const result = qualityPolicySchema.safeParse(draft);
    if (!result.success) {
      setError("请检查规则参数，补全必填阈值并确认数值范围。");
      return;
    }
    onChange({ ...document, quality_policy: result.data });
    setDraft(undefined);
  }

  return (
    <section className={styles.card} aria-label="采集方案质检规则">
      <div>
        <h3>采集方案质检规则</h3>
        <p>
          {selected == null
            ? "已启用全部质检引擎规则"
            : selected.length
              ? "已启用 " +
                selected.length +
                " 项：" +
                selected
                  .map((code) => qualityFindingText(code).title)
                  .join("、")
              : "未启用质检引擎规则"}
        </p>
      </div>
      <Button
        onClick={() => {
          setDraft(document.quality_policy);
          setQuery("");
          setCurrent(undefined);
          setError("");
        }}
      >
        选择规则
      </Button>
      <Modal
        open={draft !== undefined}
        title="选择质检规则"
        width={820}
        onCancel={() => setDraft(undefined)}
        onOk={apply}
        okText="应用到草稿"
        cancelText="取消"
        okButtonProps={{ disabled: !library || !!rules.error }}
      >
        <p>勾选参与方案质检判定的规则。录制检查和 MCAP 原始文件校验由各自的处理阶段执行。</p>
        <Space wrap>
          <Input
            aria-label="搜索质检规则"
            placeholder="搜索规则名称、代码或模板"
            value={query}
            allowClear
            onChange={(event) => setQuery(event.target.value)}
          />
          <Link to="/settings/quality-rules" target="_blank" rel="noreferrer">
            打开质检规则库
          </Link>
        </Space>
        {rules.error ? (
          <Alert
            type="error"
            title={captureError(rules.error)}
            action={<Button onClick={() => void rules.refetch()}>重试</Button>}
          />
        ) : !library ? (
          <p role="status">正在读取质检规则…</p>
        ) : (
          draft && (
            <div className={styles.layout}>
              <div
                className={styles.ruleList}
                role="group"
                aria-label="质检引擎规则"
              >
                <Space wrap>
                  <Button
                    size="small"
                    onClick={() =>
                      change({
                        enabled_codes: engines.map((item) => item.code),
                      })
                    }
                  >
                    全选
                  </Button>
                  <Button
                    size="small"
                    onClick={() => change({ enabled_codes: [] })}
                  >
                    清空
                  </Button>
                  <span>已选 {enabled.length} 项</span>
                </Space>
                {visible.map((item) => {
                  const title = qualityFindingText(item.code).title;
                  return (
                    <div
                      key={item.code}
                      className={styles.option}
                      data-current={item === rule}
                    >
                      <Checkbox
                        aria-label={"启用" + title}
                        checked={enabled.includes(item.code)}
                        onChange={(event) => {
                          setCurrent(item.code);
                          change({
                            enabled_codes: toggle(
                              item.code,
                              event.target.checked,
                            ),
                          });
                        }}
                      />
                      <button
                        type="button"
                        className={styles.choose}
                        aria-label={"编辑" + title}
                        aria-pressed={item === rule}
                        onClick={() => setCurrent(item.code)}
                      >
                        {title}
                        <ChevronRight size={14} aria-hidden />
                      </button>
                    </div>
                  );
                })}
                {!visible.length && <p>没有匹配的规则</p>}
              </div>
              {rule && (
                <div className={styles.params}>
                  <h4>{qualityFindingText(rule.code).title}</h4>
                  <p>{qualityFindingText(rule.code).description}</p>
                  {rule.group && templates.length > 0 && (
                    <Select<string>
                      aria-label="使用组织阈值模板"
                      placeholder="使用组织阈值模板"
                      value={null}
                      style={{ width: "100%" }}
                      options={templates.map((t) => ({
                        value: t.name,
                        label: t.name,
                      }))}
                      onChange={(name) => {
                        const template = templates.find((t) => t.name === name);
                        const group = rule.group;
                        if (!template || !group) return;
                        change({
                          [group]: { ...draft[group], ...template.parameters },
                          enabled_codes: toggle(rule.code, true),
                        });
                      }}
                    />
                  )}
                  <QualityFields
                    rule={rule}
                    document={{ ...document, quality_policy: draft }}
                    onChange={(next) => change(next.quality_policy)}
                  />
                </div>
              )}
            </div>
          )
        )}
        {error && <Alert type="error" title={error} />}
      </Modal>
    </section>
  );
}
