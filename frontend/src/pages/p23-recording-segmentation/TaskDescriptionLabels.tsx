import { Button, Input } from "antd";
import styles from "./styles.module.css";

export function TaskDescriptionLabels({
  labels,
  readOnly,
  onChange,
}: {
  readonly labels: readonly string[];
  readonly readOnly: boolean;
  readonly onChange: (labels: readonly string[]) => void;
}) {
  return (
    <fieldset className={styles.taskDescriptionLabels}>
      <legend>任务描述标签</legend>
      <small>默认覆盖整个 Episode，可修改或添加多条描述。</small>
      {labels.map((label, index) => (
        <div className={styles.taskDescriptionRow} key={index}>
          <Input.TextArea
            aria-label={`任务描述标签 ${index + 1}`}
            size="small"
            value={label}
            disabled={readOnly}
            maxLength={10000}
            autoSize={{ minRows: 2, maxRows: 8 }}
            placeholder="填写完整任务描述"
            onChange={(event) =>
              onChange(
                labels.map((item, position) =>
                  position === index ? event.target.value : item,
                ),
              )
            }
          />
          {!readOnly ? (
            <Button
              size="small"
              type="text"
              aria-label={`删除任务描述标签 ${index + 1}`}
              onClick={() =>
                onChange(labels.filter((_, position) => position !== index))
              }
            >
              删除
            </Button>
          ) : null}
        </div>
      ))}
      {!readOnly ? (
        <Button
          size="small"
          onClick={() => onChange([...labels, ""])}
          disabled={labels.length >= 100}
        >
          添加任务描述标签
        </Button>
      ) : null}
    </fieldset>
  );
}
