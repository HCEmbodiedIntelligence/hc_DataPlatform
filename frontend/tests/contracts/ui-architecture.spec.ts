import { readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join, relative, resolve, sep } from 'node:path';
import { describe, expect, it } from 'vitest';
import * as publicUi from '../../src/shared/ui';

const projectRoot = resolve(process.cwd());
const sourceRoot = join(projectRoot, 'src');

function sourcePath(absolutePath: string): string {
  return relative(projectRoot, absolutePath).split(sep).join('/');
}

function collectFiles(directory: string, extensions: ReadonlySet<string>): string[] {
  return readdirSync(directory)
    .flatMap((entry) => {
      const absolutePath = join(directory, entry);
      return statSync(absolutePath).isDirectory()
        ? collectFiles(absolutePath, extensions)
        : extensions.has(extname(entry))
          ? [absolutePath]
          : [];
    })
    .sort();
}

const codeFiles = collectFiles(sourceRoot, new Set(['.ts', '.tsx']));

function matchingFiles(pattern: RegExp, files = codeFiles): string[] {
  return files
    .filter((file) => pattern.test(readFileSync(file, 'utf8')))
    .map(sourcePath)
    .sort();
}

function expectShrinkingBaseline(actual: readonly string[], baseline: readonly string[]): void {
  expect(actual.filter((file) => !baseline.includes(file))).toEqual([]);
}

function filesWithTargetedAntImport(): string[] {
  const importStatement = /import\s+([\s\S]*?)\s+from\s+['"]([^'"]+)['"];?/gu;
  return codeFiles
    .filter((file) => {
      const source = readFileSync(file, 'utf8');
      return [...source.matchAll(importStatement)].some(([, bindings, moduleName]) => {
        if (moduleName === undefined || bindings === undefined) return false;
        const isAntModule =
          moduleName === 'antd' || /^antd\/(?:es\/)?(?:table|upload|popconfirm)$/u.test(moduleName);
        return isAntModule && /\b(?:Table|Upload|Popconfirm)\b/u.test(`${bindings} ${moduleName}`);
      });
    })
    .map(sourcePath)
    .sort();
}

describe('stage-one public UI architecture', () => {
  it('publishes the B4-B6 component boundary from the single shared UI entry', () => {
    const publicComponentNames = [
      'StandardPageScaffold',
      'DetailPageScaffold',
      'WorkbenchScaffold',
      'FilterToolbar',
      'EntityDrawer',
      'UiPageHeader',
      'PageState',
      'StatusTag',
      'UiMetricCard',
      'DataTable',
      'DataCursorPager',
      'RHFInput',
      'RHFSelect',
      'RHFCheckbox',
      'RHFDatePicker',
      'SecureUploadPicker',
      'DangerConfirmModal',
    ] as const;
    expect(publicComponentNames.filter((name) => typeof publicUi[name] !== 'function')).toEqual([]);
    expect(publicUi.UiPageHeader).not.toBe(publicUi.PageHeader);
    expect(publicUi.UiMetricCard).not.toBe(publicUi.MetricCard);
    expect(publicUi.DataCursorPager).not.toBe(publicUi.CursorPager);
  });

  it('keeps native dialogs and StandardTable on fixed, shrinking legacy baselines', () => {
    expectShrinkingBaseline(matchingFiles(/<dialog(?:\s|>)/u), [
      'src/pages/p02-data-sources/components/SourceActionDialogs.tsx',
      'src/pages/p02-data-sources/components/SourceEditorDialog.tsx',
      'src/pages/p03-upload-jobs/components/CreateUploadDialog.tsx',
      'src/pages/p04-upload-detail/components/DangerousUploadActionDialog.tsx',
    ]);
    expectShrinkingBaseline(matchingFiles(/\bStandardTable\b/u), [
      'src/pages/p13-storage-lifecycle/page.tsx',
      'src/pages/p14-robot-models/page.tsx',
      'src/pages/p15-robots/page.tsx',
      'src/pages/p16-calibrations/page.tsx',
      'src/pages/p17-data-schemas/page.tsx',
      'src/pages/p18-access/page.tsx',
      'src/shared/ui/StandardTable.tsx',
      'src/shared/ui/index.ts',
    ]);
  });

  it('allows Ant Table and Upload only inside their project adapters', () => {
    expectShrinkingBaseline(filesWithTargetedAntImport(), [
      'src/shared/ui/data/DataTable.tsx',
      'src/shared/ui/forms/SecureUploadPicker.tsx',
    ]);
    const uploadPicker = readFileSync(
      join(sourceRoot, 'shared/ui/forms/SecureUploadPicker.tsx'),
      'utf8',
    );
    expect(uploadPicker).toMatch(/beforeUpload=\{[\s\S]*?return false;/u);
    expect(uploadPicker).not.toMatch(/\b(?:action|customRequest)\s*=/u);
    expect(uploadPicker).not.toMatch(/\bfetch\s*\(/u);
    expect(matchingFiles(/\bPopconfirm\b/u)).toEqual([]);
  });

  it('prevents direct network, handwritten API URLs and static notification calls from spreading', () => {
    expectShrinkingBaseline(matchingFiles(/\bfetch\s*\(/u), [
      'src/features/ingest/upload/browser-oss-port.ts',
      'src/shared/api/http-client.ts',
    ]);
    expect(matchingFiles(/['"](?:https?:\/\/[^'"]+)?\/api(?:\/|['"])/u)).toEqual([]);
    expect(
      matchingFiles(/\b(?:notification|message)\.(?:open|success|error|warning|info)\s*\(/u),
    ).toEqual([]);
  });

  it('keeps role literals in the fixed catalog/projection baseline and numeric offset out of pages', () => {
    const applicationFiles = codeFiles.filter((file) =>
      /\/src\/(?:app|features|pages)\//u.test(file.split(sep).join('/')),
    );
    expectShrinkingBaseline(
      matchingFiles(/PROJECT_(?:ADMIN|DEVELOPER|DATA_PROCESSOR)/u, applicationFiles),
      [
        'src/features/access/api/index.ts',
        'src/features/access/capability-catalog.generated.ts',
        'src/features/access/capability-catalog.ts',
        'src/features/audit/api/adapter.ts',
        'src/features/audit/types.ts',
        'src/pages/p18-access/page.tsx',
        'src/pages/p18-access/query-codec.ts',
      ],
    );
    const pageFiles = codeFiles.filter((file) => file.split(sep).join('/').includes('/src/pages/'));
    expect(matchingFiles(/\b(?:offset|pageIndex|pageNumber|currentPage)\b/u, pageFiles)).toEqual(
      [],
    );
    expect(matchingFiles(/shared\/ui\/(?:layout|state|data|forms|actions)/u, pageFiles)).toEqual(
      [],
    );
  });

  it('does not expand the unscoped global CSS compatibility baseline', () => {
    const globalCssFiles = collectFiles(sourceRoot, new Set(['.css']))
      .filter((file) => !file.endsWith('.module.css'))
      .map(sourcePath)
      .sort();
    expectShrinkingBaseline(globalCssFiles, [
      'src/app/theme/global.css',
      'src/features/cleaning/cleaning.css',
      'src/features/datasets/components/datasets.css',
      'src/features/ingest/styles.css',
      'src/pages/p02-data-sources/styles.css',
      'src/pages/p04-upload-detail/styles.css',
      'src/pages/p08-data-annotation/p08.css',
      'src/pages/p13-storage-lifecycle/page.css',
      'src/pages/p14-robot-models/page.css',
      'src/pages/p15-robots/page.css',
      'src/pages/p16-calibrations/page.css',
      'src/pages/p17-data-schemas/page.css',
      'src/pages/p18-access/page.css',
      'src/shared/ui/styles.css',
    ]);
  });
});
