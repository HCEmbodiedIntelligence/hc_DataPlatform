import { expect, type Page } from "@playwright/test";
import axe from "axe-core";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

interface AxeViolation {
  readonly id: string;
  readonly impact: string | null;
  readonly description: string;
  readonly help: string;
  readonly helpUrl: string;
  readonly nodes: readonly unknown[];
}

interface AxeResult {
  readonly violations: readonly AxeViolation[];
}

interface BrowserAxe {
  run(root: Element): Promise<AxeResult>;
}

export async function assertNoSeriousOrCriticalAxe(
  page: Page,
  outputPath: string,
  rootSelector: string,
): Promise<void> {
  await page.addScriptTag({ content: axe.source });
  const result = await page.evaluate(
    async ({ selector }) => {
      const browserAxe = (window as Window & { axe?: BrowserAxe }).axe;
      const root = document.querySelector(selector);
      if (!browserAxe || !root) {
        throw new Error(`axe-core or audit root did not load: ${selector}`);
      }
      return browserAxe.run(root);
    },
    { selector: rootSelector },
  );
  const blockingViolations = result.violations.filter(
    ({ impact }) => impact === "serious" || impact === "critical",
  );
  const report = JSON.stringify(
    {
      rootSelector,
      violations: result.violations,
      blockingViolations,
    },
    null,
    2,
  );

  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, report, "utf8");
  expect(
    blockingViolations,
    `axe found serious or critical violations in ${rootSelector}; report: ${outputPath}`,
  ).toEqual([]);
}
