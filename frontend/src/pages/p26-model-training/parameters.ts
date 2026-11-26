export type JsonObject = Record<string, unknown>;
export function object(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

const keywords = new Set([
  "$schema",
  "$comment",
  "type",
  "title",
  "description",
  "default",
  "examples",
  "enum",
  "properties",
  "required",
  "additionalProperties",
  "items",
  "minItems",
  "maxItems",
  "minLength",
  "maxLength",
  "minimum",
  "maximum",
  "exclusiveMinimum",
  "exclusiveMaximum",
]);
const types = [
  "object",
  "array",
  "string",
  "boolean",
  "integer",
  "number",
  "null",
];

export function schemaIssue(
  schema: unknown,
  path = "参数",
): string | undefined {
  if (typeof schema === "boolean") return;
  if (!object(schema)) return path + " 的参数定义无效";
  if (
    schema.$schema !== undefined &&
    schema.$schema !== "https://json-schema.org/draft/2020-12/schema"
  )
    return path + " 的 JSON Schema 版本暂不支持";
  const unknown = Object.keys(schema).find((k) => !keywords.has(k));
  if (unknown) return path + " 使用了暂不支持的约束 " + unknown;
  if (schema.type !== undefined && !types.some((t) => t === schema.type))
    return path + " 使用了暂不支持的类型";
  if (
    schema.enum !== undefined &&
    (!Array.isArray(schema.enum) || !schema.enum.length)
  )
    return path + " 的枚举无效";
  if (
    schema.required !== undefined &&
    (!Array.isArray(schema.required) ||
      !schema.required.every((k) => typeof k === "string"))
  )
    return path + " 的必填字段定义无效";
  for (const key of [
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
  ]) {
    const limit = schema[key];
    if (
      limit !== undefined &&
      (typeof limit !== "number" ||
        !Number.isFinite(limit) ||
        ((key.endsWith("Items") || key.endsWith("Length")) &&
          (!Number.isInteger(limit) || limit < 0)))
    )
      return path + " 的 " + key + " 无效";
  }
  if (schema.properties !== undefined) {
    if (!object(schema.properties)) return path + " 的属性定义无效";
    for (const [key, child] of Object.entries(schema.properties)) {
      const issue = schemaIssue(child, path + "." + key);
      if (issue) return issue;
    }
  }
  for (const key of ["items", "additionalProperties"]) {
    if (schema[key] !== undefined) {
      const issue = schemaIssue(schema[key], path);
      if (issue) return issue;
    }
  }
}

function equalJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b))
    return a.length === b.length && a.every((v, i) => equalJson(v, b[i]));
  return (
    object(a) &&
    object(b) &&
    Object.keys(a).length === Object.keys(b).length &&
    Object.keys(a).every((k) => Object.hasOwn(b, k) && equalJson(a[k], b[k]))
  );
}
export function valueIssue(
  schema: unknown,
  value: unknown,
  path = "参数",
): string | undefined {
  if (schema === false) return path + " 不允许此值";
  if (!object(schema)) return;
  const actual =
    value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
  if (
    schema.type &&
    !(
      schema.type === actual ||
      (schema.type === "integer" && Number.isInteger(value))
    )
  )
    return path + " 的值类型不正确";
  if (
    Array.isArray(schema.enum) &&
    !schema.enum.some((v) => equalJson(v, value))
  )
    return path + " 请选择允许的值";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return path + " 请输入有限数值";
    if (
      (typeof schema.minimum === "number" && value < schema.minimum) ||
      (typeof schema.maximum === "number" && value > schema.maximum) ||
      (typeof schema.exclusiveMinimum === "number" &&
        value <= schema.exclusiveMinimum) ||
      (typeof schema.exclusiveMaximum === "number" &&
        value >= schema.exclusiveMaximum)
    )
      return path + " 超出允许范围";
  }
  if (typeof value === "string" || Array.isArray(value)) {
    const length = typeof value === "string" ? [...value].length : value.length;
    const [min, max] = Array.isArray(value)
      ? [schema.minItems, schema.maxItems]
      : [schema.minLength, schema.maxLength];
    if (
      (typeof min === "number" && length < min) ||
      (typeof max === "number" && length > max)
    )
      return path + " 长度不符合要求";
  }
  if (Array.isArray(value)) {
    for (const [i, item] of value.entries()) {
      const issue = valueIssue(schema.items, item, path + "[" + i + "]");
      if (issue) return issue;
    }
  }
  if (object(value)) {
    const props = object(schema.properties) ? schema.properties : {};
    if (Array.isArray(schema.required)) {
      const missing = schema.required.find((k) => !Object.hasOwn(value, k));
      if (missing !== undefined) return path + " 缺少必填项 " + missing;
    }
    for (const [key, val] of Object.entries(value)) {
      const rule = Object.hasOwn(props, key)
        ? props[key]
        : schema.additionalProperties;
      const issue = valueIssue(rule, val, path + "." + key);
      if (issue) return issue;
    }
  }
}
export function parameterDefaults(schema: JsonObject): JsonObject {
  if (object(schema.default)) return schema.default;
  if (!object(schema.properties)) return {};
  return Object.fromEntries(
    Object.entries(schema.properties)
      .filter(([, s]) => object(s) && Object.hasOwn(s, "default"))
      .map(([k, s]) => [k, (s as JsonObject).default]),
  );
}
