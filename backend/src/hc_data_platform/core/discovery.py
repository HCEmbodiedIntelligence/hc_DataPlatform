from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from fastapi import APIRouter


@dataclass(frozen=True, slots=True)
class ModuleRouter:
    """A deterministically discovered domain module router."""

    module_name: str
    router: APIRouter


def _contains_router_module(package_paths: Iterable[str], module_name: str) -> bool:
    """Check for router source without importing an unrelated domain package."""

    try:
        roots = tuple(package_paths)
    except TypeError:
        return False
    for root in roots:
        module_path = Path(root) / module_name
        if (module_path / "router.py").is_file() or (
            module_path / "router" / "__init__.py"
        ).is_file():
            return True
    return False


def discover_module_routers(package: ModuleType) -> tuple[ModuleRouter, ...]:
    """Discover ``<package>.<module>.router:router`` without a central registry."""

    package_path = getattr(package, "__path__", None)
    if package_path is None:
        raise TypeError(f"{package.__name__!r} is not a package")

    prefix = f"{package.__name__}."
    discovered: list[ModuleRouter] = []
    module_infos = sorted(pkgutil.iter_modules(package_path), key=lambda item: item.name)
    for module_info in module_infos:
        if module_info.name.startswith("_") or module_info.name == "core":
            continue
        if not module_info.ispkg or not _contains_router_module(package_path, module_info.name):
            continue
        router_module_name = f"{prefix}{module_info.name}.router"
        try:
            router_module = importlib.import_module(router_module_name)
        except ModuleNotFoundError as exc:
            if exc.name == router_module_name:
                continue
            raise
        router = getattr(router_module, "router", None)
        if router is None:
            continue
        if not isinstance(router, APIRouter):
            raise TypeError(f"{router_module_name}:router must be a fastapi.APIRouter")
        discovered.append(ModuleRouter(module_name=module_info.name, router=router))
    return tuple(discovered)


def discover_router_modules(package: ModuleType) -> tuple[APIRouter, ...]:
    """Compatibility wrapper returning only the discovered FastAPI routers."""

    return tuple(module.router for module in discover_module_routers(package))
