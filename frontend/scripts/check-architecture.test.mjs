import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { checkRepository, checkSource } from "./check-architecture.mjs";

const root = path.resolve("src");
const file = path.join(root, "shared/unsafe.ts");

for (const source of [
  'import type { Scope } from "../entities/scope";',
  'export { something } from "../pages/something";',
  'async function load() { return import("../features/something"); }',
  'type T = import("../entities/scope").Scope;',
  'const x = require("../app/routes");',
  "const x = import(computedPath);",
]) {
  test(`rejects reverse dependency: ${source}`, () => {
    assert.equal(checkSource(file, source, root).length, 1);
  });
}

test("permits shared contracts and explicitly known vendor imports", () => {
  assert.deepEqual(
    checkSource(
      file,
      'import { x } from "./scope/model"; import("zod");',
      root,
    ),
    [],
  );
});

test("current source satisfies the migrated boundary", () => {
  assert.deepEqual(checkRepository(), []);
});

for (const source of [
  'import type { Search } from "../../pages/p10/query-codec";',
  'export { Search } from "../../pages/p10/query-codec";',
  'const load = () => import("../../pages/p10/query-codec");',
]) {
  test(`features cannot acquire contracts from pages: ${source}`, () => {
    assert.equal(checkSource(path.join(root, "features/cleaning/unsafe.ts"), source, root).length, 1);
  });
}
