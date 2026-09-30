"""Executable boundaries for migrated modules, extended as consumers migrate.

No legacy allowlist is needed for object storage: old public imports remain
available for external callers/tests, but application source uses owned ports.
This is deliberately a focused gate, not a claim that all 60 audit items pass.
"""

from __future__ import annotations

import argparse
import ast
import sys
from importlib.util import resolve_name
from pathlib import Path

OBJECTS = "hc_data_platform.object_storage"
PURE_MODULES = {
    OBJECTS,
    f"{OBJECTS}.models",
    f"{OBJECTS}.ports",
    f"{OBJECTS}.checksums",
    f"{OBJECTS}.artifacts",
}
LEGACY_STORAGE = ("hc_data_platform.ingest.ports", "hc_data_platform.ingest.adapters")
ADAPTER_SUPPORT = ("hc_data_platform.core.context", "hc_data_platform.core.errors")
FORMATS = "hc_data_platform.data_formats"
LEGACY_CODECS = tuple(
    f"hc_data_platform.publishing.{name}"
    for name in (
        "depth_source",
        "depth_evidence",
        "holobrain_depth",
        "feature_names",
        "lerobot_format",
    )
) + ("hc_data_platform.lerobot_imports.feature_names",)


def matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def imports(tree: ast.Module, package: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, item.name) for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level:
                name = resolve_name("." * node.level + name, package)
            found.append((node.lineno, name))
            found.extend((node.lineno, f"{name}.{item.name}") for item in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            is_loader = (isinstance(func, ast.Name) and func.id == "__import__") or (
                isinstance(func, ast.Attribute) and func.attr == "import_module"
            )
            if is_loader:
                value = node.args[0] if node.args else None
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    name = value.value
                    found.append(
                        (node.lineno, resolve_name(name, package) if name.startswith(".") else name)
                    )
                else:
                    found.append((node.lineno, "<dynamic-import>"))
    return found


def check_source(module: str, source: str, *, is_package: bool = False) -> list[str]:
    package = module if is_package else module.rsplit(".", 1)[0]
    failures: set[str] = set()
    tree = ast.parse(source)
    if module == "hc_data_platform.publishing.postgres":
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and any(
                    foreign in node.value.lower()
                    for foreign in ("dataset_registry.", "access_control.")
                )
            ):
                failures.add(
                    f"{node.lineno}: publication storage cannot access another domain's tables"
                )
    for line, target in imports(tree, package):
        if module == "hc_data_platform.publishing.postgres" and any(
            matches(target, "hc_data_platform." + domain)
            for domain in ("dataset_registry", "continuous_recordings", "integrations")
        ):
            failures.add(
                f"{line}: publication storage must consume transaction ports, not {target}"
            )
        if (
            module
            in {
                "hc_data_platform.dataset_registry.publication",
                "hc_data_platform.continuous_recordings.publication",
            }
            and target.split(".", 1)[0] not in sys.stdlib_module_names
        ):
            failures.add(
                f"{line}: publication participant contract must remain independent of {target}"
            )
        if matches(module, "hc_data_platform.capabilities") and not (
            target.split(".", 1)[0] in sys.stdlib_module_names
            or matches(target, "hc_data_platform.capabilities")
        ):
            failures.add(f"{line}: capability registry must be independent of {target}")
        if any(matches(target, prefix) for prefix in LEGACY_CODECS):
            failures.add(f"{line}: format consumers must use data_formats, not {target}")
        if matches(module, FORMATS) and (
            target == "<dynamic-import>"
            or target.startswith("hc_data_platform.")
            and not (matches(target, FORMATS) or matches(target, "hc_data_platform.core.errors"))
        ):
            failures.add(f"{line}: format codecs must not depend on domain {target}")
        if matches(module, "hc_data_platform.episode_store") and matches(
            target, "hc_data_platform.publishing"
        ):
            failures.add(f"{line}: persistence must not import publishing {target}")
        if module in {
            "hc_data_platform.publishing.service",
            "hc_data_platform.publishing.lerobot_export",
        } and matches(target, "hc_data_platform.publishing.exporters"):
            failures.add(
                f"{line}: orchestration/archive must not import concrete exporters {target}"
            )
        if matches(target, "hc_data_platform.workflow.projection_store") and not (
            module == "hc_data_platform.runtime"
            and target
            in {
                "hc_data_platform.workflow.projection_store",
                "hc_data_platform.workflow.projection_store.S3ProjectionStagingSweeper",
            }
        ):
            failures.add(f"{line}: artifact consumers must use object_storage, not {target}")
        if matches(module, "hc_data_platform.aligned_media") and matches(
            target, "hc_data_platform.workflow"
        ):
            failures.add(f"{line}: media must not depend on workflow {target}")
        if any(matches(target, prefix) for prefix in LEGACY_STORAGE):
            failures.add(f"{line}: application source must use object_storage, not {target}")
        if not matches(module, OBJECTS):
            continue
        if module in PURE_MODULES:
            allowed = (
                target.split(".", 1)[0] in sys.stdlib_module_names
                or any(matches(target, prefix) for prefix in PURE_MODULES - {OBJECTS})
                or target == OBJECTS
            )
            # Importing a provider through the package is still an implementation dependency.
            if not allowed:
                failures.add(f"{line}: pure object contract cannot import {target}")
        elif target.startswith("hc_data_platform.") and not (
            matches(target, OBJECTS) or any(matches(target, prefix) for prefix in ADAPTER_SUPPORT)
        ):
            failures.add(f"{line}: object adapter cannot depend on domain {target}")
    return sorted(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root", type=Path, default=Path(__file__).resolve().parents[1] / "src"
    )
    options = parser.parse_args()
    root = options.source_root
    files = sorted((root / "hc_data_platform").rglob("*.py"))
    if not files:
        parser.error("source root must contain hc_data_platform Python modules")
    failures = []
    for file in files:
        parts = file.relative_to(root).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if is_package else parts)
        try:
            failures.extend(
                f"{file.relative_to(root)}:{failure}"
                for failure in check_source(module, file.read_text(), is_package=is_package)
            )
        except (SyntaxError, ImportError, ValueError) as exc:
            failures.append(f"{file.relative_to(root)}: unresolved source: {exc}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Migrated architecture boundaries passed across {len(files)} Python source files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
