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

for (const layer of ["features", "pages"]) {
  for (const source of [
    'import { useToast } from "../../app/providers/ToastProvider";',
    'export type { Routes } from "../../app/router";',
    'const load = () => import("../../app/router");',
  ]) {
    test(`${layer} cannot depend on application composition: ${source}`, () => {
      assert.equal(
        checkSource(path.join(root, layer, "example/unsafe.tsx"), source, root)
          .length,
        1,
      );
    });
  }
}

test("page links cannot import component route assembly", () => {
  assert.equal(
    checkSource(
      path.join(root, "pages/example/page.tsx"),
      'import { annotationRoutes } from "../p08-data-annotation/routes";',
      root,
    ).some((failure) =>
      failure.includes("page consumers must use URL builders"),
    ),
    true,
  );
});

for (const source of [
  'import { nativeRoot } from "../p03-upload-jobs/lerobot-processing";',
  'import type { CollectionTask } from "../p20-collection-tasks/api";',
  'const load = () => import("../p03-upload-jobs/components/OriginalSourceBrowser");',
  'import workspace from "../ui-011e/workspace.module.css";',
  'export { Component } from "../p04-upload-detail/formal-page";',
]) {
  test(`pages cannot reuse another page as a feature: ${source}`, () => {
    assert.equal(
      checkSource(
        path.join(root, "pages/p21-data-export/unsafe.tsx"),
        source,
        root,
      ).length,
      1,
    );
  });
}

test("pages compose feature capabilities and their own private components", () => {
  assert.deepEqual(
    checkSource(
      path.join(root, "pages/p21-data-export/page.tsx"),
      'import { api } from "../../features/exports/api"; import("./PackageBatchPanel");',
      root,
    ),
    [],
  );
});

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

for (const feature of ["robots", "robot-models"]) {
  for (const source of [
    'import { useEffect } from "react";',
    'import { create } from "zustand";',
    'import { useQuery } from "@tanstack/react-query";',
    'import type { Scope } from "../../../shared/scope/shell-store";',
    'export { getCurrentOrganizationId } from "../../../shared/scope/current-organization";',
    'const load = () => import("../../robots/api");',
    'type Api = import("../../robot-models/api/hooks").Api;',
  ]) {
    test(`pure ${feature} client rejects session coupling: ${source}`, () => {
      assert.equal(
        checkSource(
          path.join(root, `features/${feature}/api/client.ts`),
          source,
          root,
        ).length,
        1,
      );
    });
  }
}

test("pure scoped cache keys cannot acquire active shell selection", () => {
  assert.equal(
    checkSource(
      path.join(root, "shared/api/scoped-query-keys.ts"),
      'import { getShellState } from "../scope/shell-store";',
      root,
    ).length,
    1,
  );
});

for (const source of [
  'import type { Search } from "../../pages/p10/query-codec";',
  'export { Search } from "../../pages/p10/query-codec";',
  'const load = () => import("../../pages/p10/query-codec");',
]) {
  test(`features cannot acquire contracts from pages: ${source}`, () => {
    assert.equal(
      checkSource(path.join(root, "features/cleaning/unsafe.ts"), source, root)
        .length,
      1,
    );
  });
}
