// Response projections for migrated consumers, from the same runtime document as
// platform.ts. Unsupported schema vocabulary fails generation, never becomes any.
const responseRoots = [
  "CollectionReleasePreflight",
  "CollectionRelease",
  "LeRobotCapabilities",
];
const annotations = [
  "title",
  "description",
  "default",
  "examples",
  "deprecated",
];

export function renderRuntimeWires(
  schemas,
  runtimeSha256,
  roots = responseRoots,
) {
  const completed = new Set();
  const visiting = new Set();
  const declarations = [];
  const fail = (location, message) => {
    throw new Error(`runtime response schema ${location}: ${message}`);
  };
  function vocabulary(schema, keys, location) {
    for (const key of Object.keys(schema))
      if (![...annotations, ...keys].includes(key))
        fail(location, `unsupported keyword ${key}`);
  }
  function reference(name) {
    if (!/^[A-Za-z][A-Za-z0-9_]*$/u.test(name))
      fail(name, "invalid component name");
    if (visiting.has(name))
      fail(name, "recursive response schema requires an explicit adapter");
    if (completed.has(name)) return `${name}Wire`;
    if (!Object.hasOwn(schemas, name))
      fail(name, "missing referenced component");
    visiting.add(name);
    const expression = render(schemas[name], name);
    visiting.delete(name);
    completed.add(name);
    declarations.push(
      `export const ${name}Wire = ${expression} satisfies z.ZodType<components["schemas"][${JSON.stringify(name)}]>;`,
    );
    return `${name}Wire`;
  }
  function render(schema, location) {
    if (!schema || typeof schema !== "object" || Array.isArray(schema))
      fail(location, "expected a schema object");
    if (schema.$ref) {
      vocabulary(schema, ["$ref"], location);
      const match = /^#\/components\/schemas\/([^/]+)$/u.exec(schema.$ref);
      if (!match)
        fail(location, "only local component references are supported");
      return reference(match[1]);
    }
    if (schema.anyOf) {
      vocabulary(schema, ["anyOf"], location);
      if (!Array.isArray(schema.anyOf) || schema.anyOf.length < 2)
        fail(location, "anyOf requires at least two branches");
      return `z.union([${schema.anyOf.map((item, i) => render(item, `${location}/anyOf/${i}`)).join(", ")}])`;
    }
    const scalar = ["const", "enum"];
    let result;
    switch (schema.type) {
      case "object": {
        vocabulary(
          schema,
          ["type", "properties", "required", "additionalProperties"],
          location,
        );
        const properties = schema.properties ?? {};
        const required = new Set(schema.required ?? []);
        for (const key of required)
          if (!Object.hasOwn(properties, key))
            fail(location, `required property ${key} is missing`);
        const fields = Object.entries(properties).map(([key, value]) => {
          // Like openapi-typescript's defaultNonNullable, response defaults must
          // be present. Never manufacture a missing receipt/capability field.
          const optional =
            !required.has(key) && !Object.hasOwn(value, "default");
          return `${JSON.stringify(key)}: ${render(value, `${location}/${key}`)}${optional ? ".optional()" : ""}`;
        });
        const additional = schema.additionalProperties;
        if (!fields.length && additional && typeof additional === "object") {
          result = `z.record(z.string(), ${render(additional, `${location}/*`)})`;
        } else {
          result = `z.object({${fields.join(", ")}})`;
          if (additional === true) result += ".catchall(z.unknown())";
          else if (additional && typeof additional === "object")
            result += `.catchall(${render(additional, `${location}/*`)})`;
          // Read projection: strip future response fields, as existing clients
          // do, even for closed server models. This is not a request validator.
        }
        break;
      }
      case "array":
        vocabulary(schema, ["type", "items", "minItems", "maxItems"], location);
        result = `z.array(${render(schema.items, `${location}/items`)})`;
        if (schema.minItems !== undefined) result += `.min(${schema.minItems})`;
        if (schema.maxItems !== undefined) result += `.max(${schema.maxItems})`;
        break;
      case "string":
        vocabulary(
          schema,
          ["type", ...scalar, "minLength", "maxLength", "pattern", "format"],
          location,
        );
        if (schema.format && schema.format !== "date-time")
          fail(location, `unsupported format ${schema.format}`);
        result = schema.format
          ? "z.iso.datetime({ offset: true })"
          : "z.string()";
        if (schema.minLength !== undefined)
          result += `.min(${schema.minLength})`;
        if (schema.maxLength !== undefined)
          result += `.max(${schema.maxLength})`;
        if (schema.pattern !== undefined) {
          new RegExp(schema.pattern, "u");
          result += `.regex(new RegExp(${JSON.stringify(schema.pattern)}, "u"))`;
        }
        break;
      case "number":
      case "integer":
        vocabulary(
          schema,
          [
            "type",
            ...scalar,
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
          ],
          location,
        );
        result = schema.type === "integer" ? "z.number().int()" : "z.number()";
        for (const [key, method] of [
          ["minimum", "min"],
          ["maximum", "max"],
          ["exclusiveMinimum", "gt"],
          ["exclusiveMaximum", "lt"],
        ])
          if (schema[key] !== undefined) {
            if (
              typeof schema[key] !== "number" ||
              !Number.isFinite(schema[key])
            )
              fail(location, `${key} must be numeric (OpenAPI 3.1)`);
            result += `.${method}(${schema[key]})`;
          }
        break;
      case "boolean":
      case "null":
        vocabulary(schema, ["type", ...scalar], location);
        result = schema.type === "boolean" ? "z.boolean()" : "z.null()";
        break;
      default:
        fail(location, `unsupported type ${String(schema.type)}`);
    }
    if (Object.hasOwn(schema, "const"))
      result += `.pipe(z.literal(${JSON.stringify(schema.const)}))`;
    if (schema.enum) {
      if (!Array.isArray(schema.enum) || schema.enum.length === 0)
        fail(location, "enum cannot be empty");
      result += `.pipe(z.literal(${JSON.stringify(schema.enum)}))`;
    }
    return result;
  }
  for (const name of roots) reference(name);
  return [
    "// AUTO-GENERATED — DO NOT EDIT",
    "// Source: production-composed runtime create_app(...).openapi()",
    `// Runtime-OpenAPI-SHA256: ${runtimeSha256}`,
    "// Response projections: unknown object fields are stripped; defaults are not filled.",
    'import { z } from "zod";',
    'import type { components } from "./platform";',
    "",
    ...declarations,
    "",
  ].join("\n");
}

export async function generateRuntimeWires(
  contract,
  sha256,
  roots = responseRoots,
) {
  // Reuse the installed OpenAPI parser/bundler; no second YAML parser or schema
  // file. The transform callback leaves TypeScript generation unchanged.
  const { default: openapiTS } = await import("openapi-typescript");
  const schemas = Object.create(null);
  await openapiTS(contract, {
    silent: true,
    transform(schema, metadata) {
      const match = /^#\/components\/schemas\/([^/]+)$/u.exec(
        metadata.path ?? "",
      );
      if (match) schemas[match[1]] = schema;
    },
  });
  const { format } = await import("prettier");
  return format(renderRuntimeWires(schemas, sha256, roots), {
    parser: "typescript",
  });
}
