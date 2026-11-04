import { Button, Checkbox, Input, Modal } from "antd";
import { ChevronRight } from "lucide-react";
import { useId, useRef, useState } from "react";
import { type ProcessingOperation, type SchemeDocument } from "./contracts";
import {
  initialRows,
  labels,
  parseRow,
  sample,
  type Kind,
  type RuleRow,
} from "./rule-options";
import styles from "./postprocessing.module.css";

export function PostprocessingRules({
  document,
  onChange,
}: {
  document: SchemeDocument;
  onChange: (next: SchemeDocument) => void;
}) {
  const adapter = document.adapters.find((a) => a.source_format === "MCAP");
  const cfg = adapter?.configuration;
  const active = cfg ? initialRows(cfg).filter((r) => r.enabled) : [];
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<RuleRow[]>([]);
  const [selected, select] = useState<Kind>("zero_joints");
  const row = rows.find((r) => r.kind === selected);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const sequence = useRef(0);
  const id = useId();
  function patch(
    kind: Kind,
    changes: Partial<Pick<RuleRow, "text" | "enabled">>,
  ) {
    sequence.current++;
    setRows((prev) =>
      prev.map((r) => (r.kind === kind ? { ...r, ...changes } : r)),
    );
    setError("");
  }
  function close() {
    sequence.current++;
    setOpen(false);
  }
  function focus(kind: Kind) {
    sequence.current++;
    select(kind);
    setError("");
  }
  function apply() {
    if (!cfg || !adapter) return;
    const ops: ProcessingOperation[] = [];
    for (const r of rows) {
      try {
        ops.push(parseRow(r, cfg));
      } catch (e) {
        focus(r.kind);
        setError(e instanceof Error ? e.message : "规则格式有误。");
        return;
      }
    }
    const zero = ops.find((o) => o.kind === "zero_joints" && o.enabled);
    const copy = ops.find((o) => o.kind === "action_from_state" && o.enabled);
    if (zero?.kind === "zero_joints" && copy?.kind === "action_from_state") {
      const conflict = copy.axes.find((n) => zero.axes.includes(n));
      if (conflict) {
        focus(copy.kind);
        setError("关节 " + conflict + " 已启用置零，不能同时跟随状态。");
        return;
      }
    }
    onChange({
      ...document,
      adapters: document.adapters.map((a) =>
        a === adapter
          ? {
              ...adapter,
              configuration: {
                ...cfg,
                postprocessing_rules: { version: 2, rules: ops },
              },
            }
          : a,
      ),
    });
    close();
  }
  return (
    <section className={styles.card} aria-label="数据后处理规则">
      <div>
        <h3>数据后处理规则</h3>
        <p>
          {!cfg?.normalization
            ? "当前方案不支持此规则。"
            : (active.map((r) => labels[r.kind]).join("、") || "未启用规则") +
              " · 质检前执行"}
        </p>
      </div>
      {cfg?.normalization && (
        <>
          <Button
            onClick={() => {
              setRows(initialRows(cfg));
              focus("zero_joints");
              setOpen(true);
            }}
          >
            选择规则
          </Button>
          <Modal
            open={open}
            title="数据后处理规则"
            width={760}
            onCancel={close}
            onOk={apply}
            okText="应用到草稿"
            cancelText="取消"
          >
            <div className={styles.layout}>
              <div
                className={styles.options}
                role="group"
                aria-label="常用规则"
              >
                <h4>常用规则</h4>
                {rows.map((r) => (
                  <div
                    key={r.kind}
                    className={styles.option}
                    data-current={r.kind === selected}
                  >
                    <Checkbox
                      aria-label={"启用" + labels[r.kind]}
                      checked={r.enabled}
                      onChange={(e) => {
                        focus(r.kind);
                        patch(r.kind, { enabled: e.target.checked });
                      }}
                    />
                    <button
                      type="button"
                      className={styles.choose}
                      aria-pressed={selected === r.kind}
                      aria-label={"编辑" + labels[r.kind]}
                      onClick={() => focus(r.kind)}
                    >
                      {labels[r.kind]}
                      <ChevronRight size={14} aria-hidden />
                    </button>
                  </div>
                ))}
              </div>
              {row && (
                <div className={styles.params}>
                  <div className={styles.toolbar}>
                    <label htmlFor={id}>{labels[row.kind]} · JSON</label>
                    <div className={styles.actions}>
                      <Button
                        type="text"
                        onClick={() =>
                          patch(row.kind, { text: sample(row.kind) })
                        }
                      >
                        载入示例
                      </Button>
                      <Button
                        type="text"
                        onClick={() => fileRef.current?.click()}
                      >
                        导入 JSON
                      </Button>
                    </div>
                  </div>
                  <input
                    ref={fileRef}
                    type="file"
                    hidden
                    accept=".json,application/json"
                    onChange={async (e) => {
                      const file = e.currentTarget.files?.[0];
                      e.currentTarget.value = "";
                      if (!file) return;
                      const seq = ++sequence.current;
                      if (file.size > 65536) {
                        setError("请选择不超过 64 KiB 的 JSON 文件。");
                        return;
                      }
                      try {
                        const text = await file.text();
                        if (seq === sequence.current) patch(row.kind, { text });
                      } catch {
                        if (seq === sequence.current)
                          setError("文件读取失败，请重新选择。");
                      }
                    }}
                  />
                  <Input.TextArea
                    id={id}
                    className={styles.code}
                    spellCheck={false}
                    autoCapitalize="off"
                    autoCorrect="off"
                    value={row.text}
                    aria-invalid={!!error}
                    aria-describedby={error ? id + "-error" : undefined}
                    onChange={(e) => patch(row.kind, { text: e.target.value })}
                  />
                  {error && (
                    <p className={styles.error} id={id + "-error"} role="alert">
                      {error}
                    </p>
                  )}
                </div>
              )}
            </div>
          </Modal>
        </>
      )}
    </section>
  );
}
