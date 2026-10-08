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
LEGACY_STORAGE_EXPORTS = tuple(
    f"hc_data_platform.ingest.{name}"
    for name in (
        "ObjectStoragePort",
        "InMemoryObjectStorage",
        "S3ObjectStorage",
        "OssObjectStorage",
    )
)
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
) + (
    "hc_data_platform.lerobot_imports.feature_names",
    "hc_data_platform.continuous_recordings.openarm_raw.video.TrainingVideo",
)


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
    lazy_packages = {
        "alignment",
        "audit_projection",
        "backup",
        "collection_tasks",
        "dashboard",
        "lance_catalog",
        "publishing",
        "quality",
        "verification",
        "workflow",
    }
    if is_package and module.removeprefix("hc_data_platform.") in lazy_packages:
        # TYPE_CHECKING imports remain visible to type checkers without runtime effects.
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and (
                node.level or (node.module or "").startswith("hc_data_platform.")
            ):
                failures.add(f"{node.lineno}: package compatibility exports must be lazy")
    helper_boundaries = {
        "hc_data_platform.lance_catalog.models": ("hc_data_platform.lance_catalog.schema",),
        "hc_data_platform.lance_catalog.schema_spec": ("hc_data_platform", "pyarrow"),
        "hc_data_platform.lerobot_imports.capture": ("hc_data_platform.lerobot_imports.reader",),
        "hc_data_platform.lerobot_imports.local_files": ("hc_data_platform",),
        "hc_data_platform.continuous_recordings.openarm_raw.named": (
            "hc_data_platform.continuous_recordings.openarm_raw.alignment",
        ),
        "hc_data_platform.continuous_recordings.openarm_raw.clock_diagnostics": (
            "hc_data_platform",
            "numpy",
        ),
    }
    for line, target in imports(tree, package):
        if any(matches(target, forbidden) for forbidden in helper_boundaries.get(module, ())):
            failures.add(f"{line}: shared helper must not reintroduce its caller dependency")
    if module in {
        "hc_data_platform.episode_store.commit",
        "hc_data_platform.episode_store.delivery",
    }:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and (
                    node.func.attr
                    in {
                        "create_multipart",
                        "upload_part_stream",
                        "complete_multipart",
                        "abort_multipart",
                    }
                )
            ):
                failures.add(
                    f"{node.lineno}: Episode publication must consume ImmutableObjectWriter"
                )
    if module in {
        "hc_data_platform.episode_store.collection_delivery",
        "hc_data_platform.episode_store.derivation_service",
    }:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"_freeze", "_snapshot"}:
                failures.add(f"{node.lineno}: consume the public AnnotationSelectionPort")
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
    if module == "hc_data_platform.dataset_registry.tag_projection":
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                failures.add(f"{node.lineno}: Tag projection must consume public injected ports")
    if module == "hc_data_platform.robot_ingest.recording_bridge":
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and any(
                    table in node.value.lower()
                    for table in (
                        "ingest.recording_uploads",
                        "ingest.recording_upload_assets",
                        "ingest.continuous_recordings",
                    )
                )
            ):
                failures.add(
                    f"{node.lineno}: Raw bridge must enlist the recording registration port"
                )
    for line, target in imports(tree, package):
        if module == "hc_data_platform.robot_ingest.recording_bridge" and target in {
            "hc_data_platform.continuous_recordings.asset_repository._asset_values",
            "hc_data_platform.continuous_recordings.repository._recording_values",
        }:
            failures.add(f"{line}: Raw bridge cannot consume recording repository private codecs")
        if module in {
            "hc_data_platform.dataset_registry.tag_projection",
            "hc_data_platform.dataset_registry.projection_writer",
            "hc_data_platform.dataset_registry.projection_models",
        } and matches(target, "hc_data_platform.dataset_registry.ingest_projection"):
            failures.add(f"{line}: projection ownership must not depend back on ingest service")
        if module in {
            "hc_data_platform.backup.composition",
            "hc_data_platform.backup.restore_cli",
        } and matches(target, "hc_data_platform.backup.whole_cli"):
            failures.add(f"{line}: backup/restore must consume shared public composition")
        if module == "hc_data_platform.backup.composition" and target.endswith("_cli"):
            failures.add(f"{line}: shared backup composition cannot depend on command entrypoints")
        if module == "hc_data_platform.episode_store.derivation_service" and (
            target.endswith(".PostgresDatasetIngestProjector")
            or target.endswith(".PostgresAutomaticAnnotationRepository")
            or matches(target, "hc_data_platform.dataset_registry.tag_projection")
        ):
            failures.add(f"{line}: Tag dependencies must be injected by composition")
        if module == "hc_data_platform.episode_store.collection_delivery" and matches(
            target, "hc_data_platform.episode_store.delivery"
        ):
            failures.add(f"{line}: collection archive must consume delivery_inventory")
        if module in {
            "hc_data_platform.annotation.selection_ports",
            "hc_data_platform.annotation.revision_models",
            "hc_data_platform.annotation.delivery_models",
        } and any(
            matches(target, name)
            for name in (
                "hc_data_platform.annotation.revision_files",
                "hc_data_platform.annotation.delivery",
                "hc_data_platform.episode_store.annotated_delivery",
            )
        ):
            failures.add(f"{line}: annotation selection contracts must remain pure")

        if target in {
            "hc_data_platform.episode_store.postgres.PackageConflict",
            "hc_data_platform.episode_store.postgres.assert_scope",
            "hc_data_platform.episode_store.collections.collection_scope",
        }:
            failures.add(f"{line}: Episode contracts must use errors/scope, not {target}")
        if module in {
            "hc_data_platform.episode_store.delivery_router",
            "hc_data_platform.episode_store.derivation_router",
        } and matches(target, "hc_data_platform.episode_store.router"):
            failures.add(f"{line}: child routers must consume application dependencies")
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
        if matches(module, "hc_data_platform.episode_store") and matches(
            target, "hc_data_platform.continuous_recordings.openarm_raw.video"
        ):
            failures.add(f"{line}: persistence must use neutral data_formats codecs, not {target}")
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
        if any(matches(target, prefix) for prefix in LEGACY_STORAGE + LEGACY_STORAGE_EXPORTS):
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
    sources = []
    for file in files:
        parts = file.relative_to(root).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if is_package else parts)
        sources.append((file, module, is_package, file.relative_to(root)))
    backend = Path(__file__).resolve().parents[1]
    if root.resolve() == (backend / "src").resolve():
        for directory in (backend / "tools", backend / "scripts"):
            for file in sorted(directory.rglob("*.py")):
                parts = file.relative_to(backend).with_suffix("").parts
                sources.append((file, ".".join(parts), False, file.relative_to(backend)))
    failures = []
    for file, module, is_package, relative in sources:
        try:
            failures.extend(
                f"{relative}:{failure}"
                for failure in check_source(module, file.read_text(), is_package=is_package)
            )
        except (SyntaxError, ImportError, ValueError) as exc:
            failures.append(f"{relative}: unresolved source: {exc}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(
        f"Migrated architecture boundaries passed across {len(sources)} Python source/tool files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
