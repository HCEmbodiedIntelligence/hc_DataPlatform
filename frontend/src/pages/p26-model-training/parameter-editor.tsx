import { useState } from "react";
import {
  Button,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Switch,
  Typography,
} from "antd";
import { object, type JsonObject } from "./parameters";

export function ParameterEditor({
  schema,
  raw,
  values,
  invalidJson,
  disabled,
  onChange,
}: {
  schema: JsonObject;
  raw: string;
  values: JsonObject;
  invalidJson: boolean;
  disabled: boolean;
  onChange: (raw: string) => void;
}) {
  const props = object(schema.properties) ? schema.properties : {};
  const simple =
    Object.keys(props).length > 0 &&
    Object.values(props).every(
      (s) =>
        object(s) &&
        (Array.isArray(s.enum) ||
          ["string", "boolean", "number", "integer"].includes(String(s.type))),
    );
  const [rawMode, setRawMode] = useState(!simple);
  function update(key: string, value: unknown) {
    const next = { ...values, [key]: value };
    if (value === undefined) delete next[key];
    onChange(JSON.stringify(next, null, 2));
  }
  function control(key: string, s: JsonObject, label: string) {
    const value = Object.hasOwn(values, key) ? values[key] : undefined;
    if (
      s.type === "boolean" &&
      !Array.isArray(s.enum) &&
      typeof value === "boolean"
    ) {
      return (
        <Space>
          <Switch
            aria-label={label}
            disabled={disabled}
            checked={value}
            onChange={(v) => update(key, v)}
          />
          {!(
            Array.isArray(schema.required) && schema.required.includes(key)
          ) && (
            <Button
              type="link"
              disabled={disabled}
              onClick={() => update(key, undefined)}
            >
              清除
            </Button>
          )}
        </Space>
      );
    }
    const choices = Array.isArray(s.enum)
      ? s.enum
      : s.type === "boolean"
        ? [true, false]
        : null;
    if (choices)
      return (
        <Select
          aria-label={label}
          allowClear
          disabled={disabled}
          value={
            value === undefined
              ? undefined
              : choices.findIndex(
                  (x) => JSON.stringify(x) === JSON.stringify(value),
                )
          }
          options={choices.map((v, i) => ({
            value: i,
            label:
              typeof v === "string"
                ? v
                : v === true
                  ? "是"
                  : v === false
                    ? "否"
                    : JSON.stringify(v),
          }))}
          onChange={(i) => update(key, i == null ? undefined : choices[i])}
        />
      );
    if (s.type === "number" || s.type === "integer")
      return (
        <InputNumber
          aria-label={label}
          style={{ width: "100%" }}
          value={typeof value === "number" ? value : null}
          disabled={disabled}
          min={typeof s.minimum === "number" ? s.minimum : undefined}
          max={typeof s.maximum === "number" ? s.maximum : undefined}
          onChange={(v) => update(key, v ?? undefined)}
        />
      );
    return (
      <Input
        aria-label={label}
        disabled={disabled}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => update(key, e.target.value)}
      />
    );
  }
  return (
    <>
      <Space wrap>
        <Typography.Text strong>执行参数</Typography.Text>
        {simple && (
          <Button
            type="link"
            disabled={disabled || (rawMode && invalidJson)}
            onClick={() => setRawMode(!rawMode)}
          >
            {rawMode ? "按字段编辑" : "编辑 JSON"}
          </Button>
        )}
      </Space>
      {rawMode ? (
        <Form.Item
          label="参数 JSON"
          extra={
            <details>
              <summary>查看方案参数格式</summary>
              <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                {JSON.stringify(schema, null, 2)}
              </pre>
            </details>
          }
        >
          <Input.TextArea
            aria-label="参数 JSON"
            disabled={disabled}
            value={raw}
            autoSize={{ minRows: 3, maxRows: 15 }}
            onChange={(e) => onChange(e.target.value)}
          />
        </Form.Item>
      ) : (
        <Row gutter={16}>
          {Object.entries(props).map(([key, s]) => {
            if (!object(s)) return null;
            const label = typeof s.title === "string" ? s.title : key;
            return (
              <Col key={key} xs={24} md={12}>
                <Form.Item
                  label={label}
                  required={
                    Array.isArray(schema.required) &&
                    schema.required.includes(key)
                  }
                  extra={
                    typeof s.description === "string"
                      ? s.description
                      : undefined
                  }
                >
                  {control(key, s, label)}
                </Form.Item>
              </Col>
            );
          })}
        </Row>
      )}
    </>
  );
}
