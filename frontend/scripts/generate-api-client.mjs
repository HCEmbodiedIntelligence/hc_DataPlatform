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
const checkOnly = process.argv.slice(2).includes("--check");

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

  if (checkOnly) {
    const current = await readFile(outputPath, "utf8").catch(() => "");
    if (current !== expected) {
      process.stderr.write(
        "[gen:api] drift: frontend/src/shared/api/generated/platform.ts is not reproducible\n",
      );
      process.exitCode = 1;
    } else {
      process.stdout.write(
        `[gen:api] current: platform.ts (runtime ${runtimeSha256})\n`,
      );
    }
  } else {
    await rename(temporaryPath, outputPath);
    process.stdout.write(
      `[gen:api] generated platform.ts (runtime ${runtimeSha256})\n`,
    );
  }
} finally {
  await rm(temporaryPath, { force: true });
  await rm(runtimeContractPath, { force: true });
}
