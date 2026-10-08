import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontendRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

export function checkSource(file, content, sourceRoot, options = {}) {
  const relative = path.relative(sourceRoot, file).split(path.sep).join("/");
  const sourceLayer = relative.split("/")[0];
  const prohibitedLayers = {
    shared: ["entities", "features", "pages", "app"],
    features: ["pages", "app"],
    pages: ["app"],
  }[sourceLayer];
  // Visual fixtures explicitly compose the application test harness; they are
  // separate from production page/feature entry points.
  if (
    !prohibitedLayers ||
    /\.(test|spec)\.tsx?$/u.test(relative) ||
    relative.includes("/testing/")
  )
    return [];
  const tree = ts.createSourceFile(file, content, ts.ScriptTarget.Latest, true);
  const failures = new Set();
  function inspect(value, node) {
    const line =
      tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1;
    if (!value || !ts.isStringLiteralLike(value)) {
      failures.add(
        `${relative}:${line}: ${sourceLayer} imports must have statically known targets`,
      );
      return;
    }
    const specifier = value.text;
    if (
      relative === "features/viewer/renderer-catalog.ts" &&
      !["./renderer-contract", "./types"].includes(specifier)
    ) {
      failures.add(
        `${relative}:${line}: renderer selection must remain independent of UI, loaders and data clients: ${specifier}`,
      );
    }
    const explicitScopeContract =
      /^features\/(robots|robot-models)\/api\/client\.ts$/u.test(relative) ||
      relative === "shared/api/scoped-query-keys.ts";
    if (
      explicitScopeContract &&
      ["react", "zustand", "@tanstack/react-query"].includes(specifier)
    ) {
      failures.add(
        `${relative}:${line}: pure scoped contracts cannot depend on session UI: ${specifier}`,
      );
    }
    const resolved = ts.resolveModuleName(
      specifier,
      file,
      options,
      ts.sys,
    ).resolvedModule;
    const target =
      resolved?.resolvedFileName ??
      (specifier.startsWith(".")
        ? path.resolve(path.dirname(file), specifier)
        : null);
    if (target === null) return;
    const targetRelative = path
      .relative(sourceRoot, target)
      .split(path.sep)
      .join("/");
    if (
      explicitScopeContract &&
      (/^shared\/scope\/(shell-store|current-organization|use-)/u.test(
        targetRelative,
      ) ||
        /^features\/(robots|robot-models)\/api(?:\/(index|hooks)(?:\.tsx?)?)?$/u.test(
          targetRelative,
        ))
    ) {
      failures.add(
        `${relative}:${line}: pure scoped contracts require explicit scope and cannot depend on session UI: ${specifier}`,
      );
    }
    const layer = path.relative(sourceRoot, target).split(path.sep)[0];
    if (prohibitedLayers.includes(layer)) {
      failures.add(
        `${relative}:${line}: ${sourceLayer} cannot depend on ${specifier}`,
      );
    }
    if (sourceLayer === "pages" && layer === "pages") {
      const owner = relative.split("/")[1];
      const targetOwner = path.relative(sourceRoot, target).split(path.sep)[1];
      if (owner !== targetOwner) {
        failures.add(
          `${relative}:${line}: page ${owner} cannot depend on page ${targetOwner}: ${specifier}`,
        );
      }
    }
    if (
      sourceLayer === "pages" &&
      layer === "pages" &&
      /\/routes(?:\.tsx?)?$/u.test(target) &&
      !relative.endsWith("/routes.tsx")
    ) {
      failures.add(
        `${relative}:${line}: page consumers must use URL builders, not route assembly: ${specifier}`,
      );
    }
  }
  function visit(node) {
    if (
      relative === "features/viewer/ViewerCompositionResolver.ts" &&
      ((ts.isPropertyAccessExpression(node) && node.name.text === "modality") ||
        (ts.isElementAccessExpression(node) &&
          ts.isStringLiteralLike(node.argumentExpression) &&
          node.argumentExpression.text === "modality"))
    ) {
      const line =
        tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1;
      failures.add(
        `${relative}:${line}: composition must select renderer capabilities instead of branching on modality`,
      );
    }
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      if (node.moduleSpecifier) inspect(node.moduleSpecifier, node);
    } else if (
      ts.isImportEqualsDeclaration(node) &&
      ts.isExternalModuleReference(node.moduleReference)
    ) {
      inspect(node.moduleReference.expression, node);
    } else if (
      ts.isImportTypeNode(node) &&
      ts.isLiteralTypeNode(node.argument)
    ) {
      inspect(node.argument.literal, node);
    } else if (
      ts.isCallExpression(node) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(node.expression) &&
          node.expression.text === "require"))
    ) {
      inspect(node.arguments[0], node);
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  return [...failures];
}

function sourceFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(file);
    return /\.tsx?$/u.test(file) ? [file] : [];
  });
}

export function checkRepository(root = frontendRoot) {
  const configFile = ts.readConfigFile(
    path.join(root, "tsconfig.json"),
    ts.sys.readFile,
  );
  if (configFile.error)
    throw new Error(
      ts.flattenDiagnosticMessageText(configFile.error.messageText, "\n"),
    );
  const config = ts.parseJsonConfigFileContent(configFile.config, ts.sys, root);
  const sourceRoot = path.join(root, "src");
  return sourceFiles(sourceRoot).flatMap((file) =>
    checkSource(
      file,
      fs.readFileSync(file, "utf8"),
      sourceRoot,
      config.options,
    ),
  );
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const failures = checkRepository();
  if (failures.length) {
    process.stderr.write(failures.join("\n") + "\n");
    process.exitCode = 1;
  } else {
    process.stdout.write(
      "Frontend shared, feature/page, page/app and cross-page boundaries passed.\n",
    );
  }
}
