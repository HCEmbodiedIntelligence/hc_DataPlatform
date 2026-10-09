import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import test from "node:test";
import ts from "typescript";
import {
  generateRuntimeWires,
  renderRuntimeWires,
} from "./generate-runtime-wires.mjs";

async function load(schemas) {
  const source = renderRuntimeWires(schemas, "test-contract", ["Receipt"]);
  const js = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2023,
    },
  }).outputText;
  return (
    await import(
      `data:text/javascript;base64,${Buffer.from(js.replace('from "zod"', `from ${JSON.stringify(import.meta.resolve("zod"))}`)).toString("base64")}`
    )
  ).ReceiptWire;
}
const schema = () => ({
  Receipt: {
    type: "object",
    additionalProperties: false,
    properties: {
      state: { $ref: "#/components/schemas/State" },
      count: { type: "integer", exclusiveMinimum: 0, maximum: 3 },
      digest: { type: "string", pattern: "^[a-f0-9]{64}$" },
      version: { type: "string", const: "v1", default: "v1" },
      reason: { anyOf: [{ type: "string" }, { type: "null" }] },
    },
    required: ["state", "count", "digest"],
  },
  State: { type: "string", enum: ["PASS", "FAIL"] },
});
const receipt = {
  state: "PASS",
  count: 2,
  digest: "a".repeat(64),
  version: "v1",
};

test("generated response parses refs, nullability and defaulted output without fabricating defaults", async () => {
  const wire = await load(schema());
  assert.deepEqual(wire.parse({ ...receipt, future: true }), receipt);
  assert.equal(wire.safeParse({ ...receipt, reason: null }).success, true);
  assert.equal(
    wire.safeParse({ ...receipt, version: undefined }).success,
    false,
  );
});
test("contract enum/required/limit changes immediately change executable response validation", async () => {
  const original = await load(schema());
  const changed = schema();
  changed.State.enum.push("SKIP");
  changed.Receipt.required.push("reason");
  changed.Receipt.properties.count.maximum = 4;
  const updated = await load(changed);
  assert.equal(
    original.safeParse({ ...receipt, state: "SKIP", count: 4, reason: null })
      .success,
    false,
  );
  assert.equal(
    updated.safeParse({ ...receipt, state: "SKIP", count: 4, reason: null })
      .success,
    true,
  );
  assert.equal(original.safeParse(receipt).success, true);
  assert.equal(updated.safeParse(receipt).success, false);
});
test("wire limits, integer precision, hash and const reject malformed receipts", async () => {
  const wire = await load(schema());
  for (const change of [
    { count: 0 },
    { count: 4 },
    { count: 1.5 },
    { count: Number.MAX_SAFE_INTEGER + 1 },
    { digest: "bad" },
    { state: "UNKNOWN" },
    { version: "v2" },
  ])
    assert.equal(
      wire.safeParse({ ...receipt, ...change }).success,
      false,
      JSON.stringify(change),
    );
});
test("dictionary and array constraints survive generation", async () => {
  const wire = await load({
    Receipt: {
      type: "object",
      additionalProperties: {
        type: "array",
        minItems: 1,
        maxItems: 2,
        items: { type: "string", minLength: 1, maxLength: 3 },
      },
    },
  });
  assert.deepEqual(wire.parse({ episode: ["tag"] }), { episode: ["tag"] });
  for (const value of [
    { episode: [] },
    { episode: [""] },
    { episode: ["long"] },
    { episode: ["a", "b", "c"] },
  ])
    assert.equal(wire.safeParse(value).success, false);
});
test("date-time offsets remain checked", async () => {
  const wire = await load({ Receipt: { type: "string", format: "date-time" } });
  assert.equal(wire.safeParse("2026-10-09T12:30:00+08:00").success, true);
  assert.equal(wire.safeParse("2026-10-09T12:30:00").success, false);
});
for (const invalid of [
  { type: "object", unevaluatedProperties: false },
  { type: "array", items: { type: "string" }, uniqueItems: true },
  { type: "string", format: "unsupported-format" },
  { oneOf: [{ type: "null" }, { type: "string" }] },
  { $ref: "https://example.invalid/schema" },
  { $ref: "#/components/schemas/Missing" },
  { $ref: "#/components/schemas/Receipt" },
])
  test(`unsupported or unresolved vocabulary fails generation: ${JSON.stringify(invalid)}`, () => {
    assert.throws(
      () => renderRuntimeWires({ Receipt: invalid }, "hash", ["Receipt"]),
      /runtime response schema/u,
    );
  });
test("production OpenAPI parser callback supplies actual component constraints", async () => {
  const output = await generateRuntimeWires(
    `openapi: 3.1.0
info:
  title: Generator test
  version: '1'
paths: {}
components:
  schemas:
    Receipt:
      type: string
      enum: [PASS, SKIP]
`,
    "contract-hash",
    ["Receipt"],
  );
  assert.match(output, /Runtime-OpenAPI-SHA256: contract-hash/u);
  assert.match(output, /z.literal\(\["PASS", "SKIP"\]\)/u);
});

test("UUID response fields validate their declared format", async () => {
  const wire = await load({ Receipt: { type: "string", format: "uuid" } });
  assert.equal(
    wire.safeParse("c00a88bb-af61-59ce-8274-b05eb2b231c3").success,
    true,
  );
  assert.equal(wire.safeParse("annotation-native").success, false);
});
