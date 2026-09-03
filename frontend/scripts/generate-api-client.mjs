import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { access, readFile, rename, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDir, "..");
const repositoryRoot = path.resolve(frontendRoot, "..");
const backendRoot = path.join(repositoryRoot, "backend");
const aggregateContractPath = path.join(backendRoot, "openapi.generated.yaml");
const runtimeContractPath = path.join(
  frontendRoot,
  "src/shared/api/generated/.runtime-openapi.tmp.yaml",
);
const outputPath = path.join(
  frontendRoot,
  "src/shared/api/generated/platform.ts",
);
const temporaryPath = path.join(
  frontendRoot,
  "src/shared/api/generated/.platform.tmp.ts",
);
const operationsOutputPath = path.join(
  frontendRoot,
  "src/shared/api/generated/platform-operations.ts",
);
const temporaryOperationsPath = path.join(
  frontendRoot,
  "src/shared/api/generated/.platform-operations.tmp.ts",
);
const checkOnly = process.argv.slice(2).includes("--check");

export function runtimeOperations(contract) {
  const operations = new Map();
  let currentPath = null;
  let inPaths = false;
  for (const line of contract.split(/\r?\n/u)) {
    if (line === "paths:") {
      inPaths = true;
      continue;
    }
    if (inPaths && /^[^\s]/u.test(line)) break;
    const pathMatch = /^  (\/.+):$/u.exec(line);
    if (pathMatch) {
      currentPath = pathMatch[1].startsWith("/api/v1/") ? pathMatch[1] : null;
      continue;
    }
    // PyYAML uses an explicit complex-key form for some long path keys:
    //
    //   ? /api/v1/...
    //   : get:
    //
    // The runtime contract is authoritative, so generate the operation gate
    // from either valid YAML representation rather than silently omitting it.
    const complexPathMatch = /^  \? (\/.+)$/u.exec(line);
    if (complexPathMatch) {
      currentPath = complexPathMatch[1].startsWith("/api/v1/")
        ? complexPathMatch[1]
        : null;
      continue;
    }
    const methodMatch = /^    (get|put|post|delete|head|patch):$/u.exec(line);
    if (currentPath && methodMatch) {
      const operation = {
        method: methodMatch[1].toUpperCase(),
        path: currentPath.slice("/api/v1".length),
      };
      operations.set(`${operation.method} ${operation.path}`, operation);
    }
    const complexMethodMatch = /^  : (get|put|post|delete|head|patch):$/u.exec(
      line,
    );
    if (currentPath && complexMethodMatch) {
      const operation = {
        method: complexMethodMatch[1].toUpperCase(),
        path: currentPath.slice("/api/v1".length),
      };
      operations.set(`${operation.method} ${operation.path}`, operation);
    }
  }
  if (operations.size === 0) {
    throw new Error("runtime OpenAPI did not contain any /api/v1 operations");
  }
  return [...operations.values()].sort(
    (left, right) =>
      left.path.localeCompare(right.path) ||
      left.method.localeCompare(right.method),
  );
}

function renderOperations(operations, runtimeSha256) {
  return [
    "// AUTO-GENERATED — DO NOT EDIT",
    "// Source: production-composed runtime create_app(...).openapi()",
    `// Runtime-OpenAPI-SHA256: ${runtimeSha256}`,
    "",
    "export const runtimeOperations = [",
    ...operations.map(
      (operation) =>
        `  { method: ${JSON.stringify(operation.method)}, path: ${JSON.stringify(operation.path)} },`,
    ),
    "] as const;",
    "",
    "export type RuntimeOperation = (typeof runtimeOperations)[number];",
    "",
  ].join("\n");
}

function runProcess(executable, args, cwd) {
  return new Promise((resolve) => {
    const child = spawn(executable, args, {
      cwd,
      stdio: ["ignore", "inherit", "inherit"],
    });
    child.on("error", (error) => resolve({ ok: false, reason: error.message }));
    child.on("close", (code) =>
      resolve({
        ok: code === 0,
        reason: `openapi-typescript exited with ${String(code)}`,
      }),
    );
  });
}

async function runtimeExporter() {
  const localPython = path.join(backendRoot, ".venv/bin/python");
  try {
    await access(localPython);
    return {
      executable: localPython,
      args: [
        "-m",
        "hc_data_platform.core.openapi",
        "--runtime",
        "--output",
        runtimeContractPath,
      ],
    };
  } catch {
    const executable = process.platform === "win32" ? "uv.exe" : "uv";
    return {
      executable,
      args: [
        "run",
        "python",
        "-m",
        "hc_data_platform.core.openapi",
        "--runtime",
        "--output",
        runtimeContractPath,
      ],
    };
  }
}

export async function main() {
  try {
    const exporter = await runtimeExporter();
    const exportResult = await runProcess(
      exporter.executable,
      exporter.args,
      backendRoot,
    );
    if (!exportResult.ok) {
      throw new Error(`runtime OpenAPI export failed: ${exportResult.reason}`);
    }

    const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
    const generateResult = await runProcess(
      pnpm,
      [
        "exec",
        "openapi-typescript",
        runtimeContractPath,
        "--output",
        temporaryPath,
      ],
      frontendRoot,
    );
    if (!generateResult.ok) {
      throw new Error(generateResult.reason);
    }

    const runtimeContract = await readFile(runtimeContractPath);
    const aggregateContract = await readFile(aggregateContractPath);
    const runtimeSha256 = createHash("sha256")
      .update(runtimeContract)
      .digest("hex");
    const aggregateSha256 = createHash("sha256")
      .update(aggregateContract)
      .digest("hex");
    const generatedHeader = [
      "// AUTO-GENERATED — DO NOT EDIT",
      "// Source: production-composed runtime create_app(...).openapi()",
      `// Runtime-OpenAPI-SHA256: ${runtimeSha256}`,
      `// Fragment-Aggregate-SHA256: ${aggregateSha256}`,
      "",
    ].join("\n");
    const generated = await readFile(temporaryPath, "utf8");
    const expected =
      generatedHeader + generated.replace(/^\/\/ AUTO-GENERATED.*\n/u, "");
    await writeFile(temporaryPath, expected, "utf8");
    const expectedOperations = renderOperations(
      runtimeOperations(runtimeContract.toString("utf8")),
      runtimeSha256,
    );
    await writeFile(temporaryOperationsPath, expectedOperations, "utf8");

    if (checkOnly) {
      const [current, currentOperations] = await Promise.all([
        readFile(outputPath, "utf8").catch(() => ""),
        readFile(operationsOutputPath, "utf8").catch(() => ""),
      ]);
      if (current !== expected || currentOperations !== expectedOperations) {
        process.stderr.write(
          "[gen:api] drift: generated runtime API types or operation gate are not reproducible\n",
        );
        process.exitCode = 1;
      } else {
        process.stdout.write(
          `[gen:api] current: platform.ts (runtime ${runtimeSha256})\n`,
        );
      }
    } else {
      await Promise.all([
        rename(temporaryPath, outputPath),
        rename(temporaryOperationsPath, operationsOutputPath),
      ]);
      process.stdout.write(
        `[gen:api] generated platform.ts (runtime ${runtimeSha256})\n`,
      );
    }
  } finally {
    await rm(temporaryPath, { force: true });
    await rm(temporaryOperationsPath, { force: true });
    await rm(runtimeContractPath, { force: true });
  }
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  await main();
}
