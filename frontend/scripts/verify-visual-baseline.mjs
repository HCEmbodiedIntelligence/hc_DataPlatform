import { createHash } from "node:crypto";
import { readdir, readFile, stat, writeFile } from "node:fs/promises";
import { relative, resolve, sep } from "node:path";

function usage() {
  throw new Error(
    "Usage: node scripts/verify-visual-baseline.mjs --artifacts <directory> --baseline <file> [--write]",
  );
}

function parseArguments(argv) {
  const options = { artifacts: null, baseline: null, write: false };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--") continue;
    if (argument === "--write") {
      options.write = true;
      continue;
    }
    if (argument === "--artifacts" || argument === "--baseline") {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) usage();
      options[argument.slice(2)] = value;
      index += 1;
      continue;
    }
    usage();
  }
  if (!options.artifacts || !options.baseline) usage();
  return options;
}

async function listPngFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const child = resolve(directory, entry.name);
      if (entry.isDirectory()) return listPngFiles(child);
      if (entry.isFile() && entry.name.endsWith(".png")) return [child];
      return [];
    }),
  );
  return files.flat();
}

async function renderBaseline(artifactsDirectory) {
  const pngFiles = await listPngFiles(artifactsDirectory);
  const images = await Promise.all(
    pngFiles.map(async (file) => {
      const body = await readFile(file);
      return {
        file: relative(artifactsDirectory, file).split(sep).join("/"),
        sha256: createHash("sha256").update(body).digest("hex"),
      };
    }),
  );
  images.sort((left, right) => left.file.localeCompare(right.file));
  if (images.length === 0) {
    throw new Error("Visual baseline requires at least one PNG artifact");
  }
  return { schemaVersion: 1, images };
}

function assertBaseline(value) {
  if (
    typeof value !== "object" ||
    value === null ||
    value.schemaVersion !== 1 ||
    !Array.isArray(value.images)
  ) {
    throw new Error("Visual baseline is not a supported schemaVersion 1 document");
  }
  for (const image of value.images) {
    if (
      typeof image !== "object" ||
      image === null ||
      typeof image.file !== "string" ||
      !/^[a-f0-9]{64}$/u.test(image.sha256)
    ) {
      throw new Error("Visual baseline contains an invalid image entry");
    }
  }
}

function changedPaths(expected, observed) {
  const expectedByFile = new Map(expected.images.map((image) => [image.file, image.sha256]));
  const observedByFile = new Map(observed.images.map((image) => [image.file, image.sha256]));
  return [...new Set([...expectedByFile.keys(), ...observedByFile.keys()])]
    .filter((file) => expectedByFile.get(file) !== observedByFile.get(file))
    .sort((left, right) => left.localeCompare(right));
}

const options = parseArguments(process.argv.slice(2));
const artifactsDirectory = resolve(options.artifacts);
const baselinePath = resolve(options.baseline);
const artifacts = await stat(artifactsDirectory);
if (!artifacts.isDirectory()) {
  throw new Error("Visual artifact root must be a directory");
}
const observed = await renderBaseline(artifactsDirectory);

if (options.write) {
  await writeFile(
    baselinePath,
    `${JSON.stringify(observed, null, 2)}\n`,
    "utf8",
  );
  process.stdout.write(`VISUAL_BASELINE_WRITTEN images=${observed.images.length}\n`);
} else {
  const expected = JSON.parse(await readFile(baselinePath, "utf8"));
  assertBaseline(expected);
  const changed = changedPaths(expected, observed);
  if (changed.length > 0) {
    throw new Error(`Visual baseline mismatch: ${changed.join(", ")}`);
  }
  process.stdout.write(`VISUAL_BASELINE_VERIFIED images=${observed.images.length}\n`);
}
