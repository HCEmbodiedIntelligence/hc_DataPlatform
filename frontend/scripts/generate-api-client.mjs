import { spawn } from 'node:child_process';
import { readFile, rename, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, '..');
// Historical directory name; its files are frontend API requirement drafts, not backend implementation contracts.
const contractRoot = process.env.OPENAPI_ROOT ?? '/home/czy/plan/backend';
const outputDir = path.join(frontendRoot, 'src/shared/api/generated');
const notesPath = path.join(frontendRoot, 'docs/frontend-scaffold-notes.md');
const generatedHeader = '// AUTO-GENERATED — DO NOT EDIT\n';

const domains = {
  ingest: '01-ingest/ingest-api.openapi.yaml',
  datasets: '02-dataset-version-review/dataset-version-review-api.openapi.yaml',
  cleaning: '03-manual-cleaning/manual-cleaning-api.openapi.yaml',
  storage: '04-storage-lifecycle/storage-lifecycle-api.openapi.yaml',
  robotics: '05-robotics-calibration-schema/robotics-calibration-schema-api.openapi.yaml',
  access: '06-access-audit/access-audit-api.openapi.yaml',
  platform: '07-platform-foundation/platform-api-baseline.yaml',
  annotation: '09-data-annotation/data-annotation-api.openapi.yaml',
};

function runGenerator(input, output) {
  return new Promise((resolve) => {
    const executable = process.platform === 'win32' ? 'pnpm.cmd' : 'pnpm';
    const child = spawn(executable, ['exec', 'openapi-typescript', input, '--output', output], {
      cwd: frontendRoot,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
    });
    child.on('error', (error) => resolve({ ok: false, reason: error.message }));
    child.on('close', (code) =>
      resolve({
        ok: code === 0,
        reason: stderr.trim().split('\n').slice(-3).join(' ') || `exit code ${String(code)}`,
      }),
    );
  });
}

function safeReason(reason) {
  return reason.replaceAll(frontendRoot, '<frontend>').replaceAll(contractRoot, '<api-drafts>');
}

async function updateNotes(successes, failures) {
  const start = '<!-- generated-api-status:start -->';
  const end = '<!-- generated-api-status:end -->';
  const successLines = successes.length
    ? successes.map((domain) => `- \`${domain}.ts\``).join('\n')
    : '- 无';
  const failureLines = failures.length
    ? failures.map(({ domain, reason }) => `- \`${domain}\`: ${safeReason(reason)}`).join('\n')
    : '- 无';
  const block = `${start}\n### OpenAPI 生成状态\n\n已生成：\n\n${successLines}\n\n失败域：\n\n${failureLines}\n${end}`;
  let current = '';
  try {
    current = await readFile(notesPath, 'utf8');
  } catch {
    current = '# 前端脚手架交接说明\n\n';
  }
  const pattern = new RegExp(`${start}[\\s\\S]*?${end}`);
  const next = pattern.test(current) ? current.replace(pattern, block) : `${current.trim()}\n\n${block}\n`;
  await writeFile(notesPath, next, 'utf8');
}

const successes = [];
const failures = [];

for (const [domain, relativeInput] of Object.entries(domains)) {
  const input = path.join(contractRoot, relativeInput);
  const output = path.join(outputDir, `${domain}.ts`);
  const temporary = path.join(outputDir, `.${domain}.tmp.ts`);
  const result = await runGenerator(input, temporary);
  if (!result.ok) {
    await rm(temporary, { force: true });
    failures.push({ domain, reason: result.reason });
    process.stderr.write(`[gen:api] skipped ${domain}: ${result.reason}\n`);
    continue;
  }
  const generated = await readFile(temporary, 'utf8');
  await writeFile(temporary, generatedHeader + generated.replace(/^\/\/ AUTO-GENERATED.*\n/u, ''), 'utf8');
  await rename(temporary, output);
  successes.push(domain);
  process.stdout.write(`[gen:api] generated ${domain}.ts\n`);
}

await updateNotes(successes, failures);
process.stdout.write(`[gen:api] complete: ${successes.length} generated, ${failures.length} skipped\n`);
