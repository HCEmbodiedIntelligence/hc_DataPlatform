from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

import yaml

OpenAPIDocument = dict[str, Any]
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


class OpenAPIContractError(ValueError):
    """Base class for invalid or incompatible OpenAPI contracts."""


class DuplicateOpenAPIError(OpenAPIContractError):
    pass


class IncompatibleOpenAPIError(OpenAPIContractError):
    def __init__(self, issues: Iterable[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("incompatible OpenAPI contract:\n- " + "\n- ".join(self.issues))


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateOpenAPIError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_yaml(path: Path) -> OpenAPIDocument:
    try:
        loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise OpenAPIContractError(f"invalid YAML in {path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise OpenAPIContractError(f"{path} must contain a YAML object")
    return cast(OpenAPIDocument, loaded)


def _mapping(value: Any, *, location: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise OpenAPIContractError(f"{location} must be an object with string keys")
    return cast(dict[str, Any], value)


def _security_requirements(value: Any, *, location: str) -> list[dict[str, list[str]]]:
    if not isinstance(value, list):
        raise OpenAPIContractError(f"{location} must be an array")
    requirements: list[dict[str, list[str]]] = []
    for index, requirement in enumerate(value):
        mapping = _mapping(requirement, location=f"{location}[{index}]")
        normalized: dict[str, list[str]] = {}
        for scheme, scopes in mapping.items():
            if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
                raise OpenAPIContractError(
                    f"{location}[{index}].{scheme} must be an array of strings"
                )
            normalized[scheme] = scopes
        requirements.append(normalized)
    return requirements


def _materialize_fragment_security(
    path_item: Any,
    security: list[dict[str, list[str]]] | None,
    *,
    location: str,
) -> Any:
    if security is None:
        return path_item
    item = copy.deepcopy(_mapping(path_item, location=location))
    for method, operation_value in item.items():
        if method.lower() not in HTTP_METHODS:
            continue
        operation = _mapping(operation_value, location=f"{location}.{method}")
        if "security" not in operation:
            operation["security"] = copy.deepcopy(security)
    return item


def aggregate_fragments(fragment_dir: Path) -> OpenAPIDocument:
    """Aggregate fragments in filename order and reject ambiguous ownership."""

    if not fragment_dir.is_dir():
        raise OpenAPIContractError(f"fragment directory does not exist: {fragment_dir}")
    document: OpenAPIDocument = {
        "openapi": "3.1.0",
        "info": {"title": "HC Data Platform Backend", "version": "0.1.0"},
        "paths": {},
        "components": {},
    }
    output_paths = cast(dict[str, Any], document["paths"])
    output_components = cast(dict[str, dict[str, Any]], document["components"])

    fragment_paths = sorted(
        (*fragment_dir.glob("*.yaml"), *fragment_dir.glob("*.yml")),
        key=lambda item: item.name,
    )
    for fragment_path in fragment_paths:
        fragment = _load_yaml(fragment_path)
        fragment_security = (
            _security_requirements(
                fragment["security"],
                location=f"{fragment_path}:security",
            )
            if "security" in fragment
            else None
        )
        paths = _mapping(fragment.get("paths"), location=f"{fragment_path}:paths")
        for route in sorted(paths):
            if not route.startswith("/"):
                raise OpenAPIContractError(f"{fragment_path}: invalid path {route!r}")
            if route in output_paths:
                raise DuplicateOpenAPIError(
                    f"duplicate OpenAPI path {route!r} in {fragment_path.name}"
                )
            output_paths[route] = _materialize_fragment_security(
                paths[route],
                fragment_security,
                location=f"{fragment_path}:paths.{route}",
            )

        components = _mapping(
            fragment.get("components"),
            location=f"{fragment_path}:components",
        )
        for section_name in sorted(components):
            section = _mapping(
                components[section_name],
                location=f"{fragment_path}:components.{section_name}",
            )
            output_section = output_components.setdefault(section_name, {})
            for component_name in sorted(section):
                if component_name in output_section:
                    noun = "schema" if section_name == "schemas" else "component"
                    raise DuplicateOpenAPIError(
                        f"duplicate OpenAPI {noun} {component_name!r} in {fragment_path.name}"
                    )
                output_section[component_name] = section[component_name]

    output_components.setdefault("schemas", {})
    # Apply the same public-auth contract normalization used by app.openapi().  This keeps
    # fragments declarative while making omitted legacy fragment security explicit.
    from .app import _normalize_operation_contracts

    _normalize_operation_contracts(document)
    return document


def runtime_document() -> OpenAPIDocument:
    """Build the production-composed runtime schema used for generated client types."""

    from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate

    from .app import create_app
    from .config import Settings

    application = create_app(
        settings=Settings(environment="test", runtime_backend="production"),
        maintenance_write_gate=InMemoryMaintenanceWriteGate(),
    )
    return application.openapi()


def _resolve_component_reference(document: Mapping[str, Any], value: Any) -> Any:
    current = value
    seen: set[str] = set()
    while isinstance(current, dict):
        reference = current.get("$ref")
        if not isinstance(reference, str) or not reference.startswith("#/components/"):
            break
        parts = reference.removeprefix("#/").split("/")
        if len(parts) != 3 or reference in seen:
            break
        seen.add(reference)
        section = _mapping(document.get(parts[0]), location=parts[0])
        subsection = _mapping(section.get(parts[1]), location=f"{parts[0]}.{parts[1]}")
        current = subsection.get(parts[2], current)
    return current


def _schema_references(value: Any) -> tuple[str, ...]:
    references: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            reference = item.get("$ref")
            if isinstance(reference, str):
                references.add(reference)
            for key, child in item.items():
                if key != "$ref":
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(references))


def _media_schema_references(document: Mapping[str, Any], container: Any) -> tuple[str, ...]:
    resolved = _resolve_component_reference(document, container)
    if not isinstance(resolved, dict):
        return ()
    content = resolved.get("content", {})
    if not isinstance(content, dict):
        return ()
    references: set[str] = set()
    for media in content.values():
        if isinstance(media, dict):
            references.update(_schema_references(media.get("schema", {})))
    return tuple(sorted(references))


def formal_runtime_contract_issues(
    formal: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> tuple[str, ...]:
    """Compare the wire-significant operation identity of fragments and runtime."""

    issues: list[str] = []
    formal_paths = _mapping(formal.get("paths"), location="formal.paths")
    runtime_paths = _mapping(runtime.get("paths"), location="runtime.paths")
    for path in sorted(formal_paths.keys() - runtime_paths.keys()):
        issues.append(f"runtime is missing path {path}")
    for path in sorted(runtime_paths.keys() - formal_paths.keys()):
        issues.append(f"formal fragments are missing runtime path {path}")
    for path in sorted(formal_paths.keys() & runtime_paths.keys()):
        formal_operations = _operations(formal_paths[path])
        runtime_operations = _operations(runtime_paths[path])
        for method in sorted(formal_operations.keys() - runtime_operations.keys()):
            issues.append(f"runtime is missing {method.upper()} {path}")
        for method in sorted(runtime_operations.keys() - formal_operations.keys()):
            issues.append(f"formal fragments are missing {method.upper()} {path}")
        for method in sorted(formal_operations.keys() & runtime_operations.keys()):
            location = f"{method.upper()} {path}"
            expected = _mapping(formal_operations[method], location=f"formal {location}")
            actual = _mapping(runtime_operations[method], location=f"runtime {location}")
            if expected.get("operationId") != actual.get("operationId"):
                issues.append(f"{location}: operationId differs")
            if expected.get("security", []) != actual.get("security", []):
                issues.append(f"{location}: security differs")

            expected_request_refs = _media_schema_references(
                formal, expected.get("requestBody", {})
            )
            actual_request_refs = _media_schema_references(runtime, actual.get("requestBody", {}))
            if expected_request_refs != actual_request_refs:
                issues.append(
                    f"{location}: request schema refs differ "
                    f"({expected_request_refs!r} != {actual_request_refs!r})"
                )

            expected_responses = _mapping(
                expected.get("responses"), location=f"formal {location}.responses"
            )
            actual_responses = _mapping(
                actual.get("responses"), location=f"runtime {location}.responses"
            )
            for status in sorted(expected_responses.keys() & actual_responses.keys()):
                expected_refs = _media_schema_references(formal, expected_responses[status])
                if not expected_refs:
                    continue
                actual_refs = _media_schema_references(runtime, actual_responses[status])
                if expected_refs != actual_refs:
                    issues.append(
                        f"{location} response {status}: schema refs differ "
                        f"({expected_refs!r} != {actual_refs!r})"
                    )
    return tuple(issues)


def check_formal_runtime_contract(
    formal: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> None:
    issues = formal_runtime_contract_issues(formal, runtime)
    if issues:
        raise IncompatibleOpenAPIError(issues)


def render(document: Mapping[str, Any]) -> str:
    return yaml.safe_dump(
        dict(document),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
        width=100,
    )


def digest(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(render(document).encode("utf-8")).hexdigest()


def _operations(path_item: Any) -> dict[str, Any]:
    item = _mapping(path_item, location="path item")
    return {key: value for key, value in item.items() if key.lower() in HTTP_METHODS}


def _effective_operation(path_item: Any, operation: Any) -> dict[str, Any]:
    """Apply path-level parameters before comparing an operation."""

    path = _mapping(path_item, location="path item")
    effective = dict(_mapping(operation, location="operation"))
    path_parameters = path.get("parameters", [])
    operation_parameters = effective.get("parameters", [])
    if not isinstance(path_parameters, list) or not isinstance(operation_parameters, list):
        raise OpenAPIContractError("OpenAPI parameters must be arrays")
    effective["parameters"] = [*path_parameters, *operation_parameters]
    return effective


def _parameter_key(parameter: Any) -> tuple[str, str] | None:
    if not isinstance(parameter, dict):
        return None
    reference = parameter.get("$ref")
    if isinstance(reference, str):
        return "$ref", reference
    name = parameter.get("name")
    location = parameter.get("in")
    if isinstance(name, str) and isinstance(location, str):
        return location, name
    return None


def _reference_compatibility_issues(
    location: str,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    old_reference = previous.get("$ref")
    new_reference = current.get("$ref")
    if old_reference == new_reference:
        return []
    if old_reference is not None or new_reference is not None:
        return [f"{location}: changed reference from {old_reference!r} to {new_reference!r}"]
    return []


def _tightened_constraint_issues(
    location: str,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    issues: list[str] = []
    lower_bounds = ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties")
    upper_bounds = ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties")

    for keyword in lower_bounds:
        old_value = previous.get(keyword)
        new_value = current.get(keyword)
        if new_value is None:
            continue
        if old_value is None or (
            isinstance(old_value, int | float)
            and not isinstance(old_value, bool)
            and isinstance(new_value, int | float)
            and not isinstance(new_value, bool)
            and new_value > old_value
        ):
            issues.append(f"{location}: tightened {keyword}")
        elif old_value != new_value and not (
            isinstance(old_value, int | float) and isinstance(new_value, int | float)
        ):
            issues.append(f"{location}: changed {keyword}")

    for keyword in upper_bounds:
        old_value = previous.get(keyword)
        new_value = current.get(keyword)
        if new_value is None:
            continue
        if old_value is None or (
            isinstance(old_value, int | float)
            and not isinstance(old_value, bool)
            and isinstance(new_value, int | float)
            and not isinstance(new_value, bool)
            and new_value < old_value
        ):
            issues.append(f"{location}: tightened {keyword}")
        elif old_value != new_value and not (
            isinstance(old_value, int | float) and isinstance(new_value, int | float)
        ):
            issues.append(f"{location}: changed {keyword}")

    for keyword in ("pattern", "format"):
        old_value = previous.get(keyword)
        new_value = current.get(keyword)
        if new_value is not None and new_value != old_value:
            issues.append(f"{location}: changed {keyword}")
    return issues


def _schema_compatibility_issues_at(
    location: str,
    previous: Any,
    current: Any,
) -> list[str]:
    old = _mapping(previous, location=location)
    new = _mapping(current, location=location)
    issues = _reference_compatibility_issues(location, old, new)
    if issues:
        return issues

    if old.get("type") != new.get("type"):
        issues.append(f"{location}: changed type from {old.get('type')!r} to {new.get('type')!r}")

    old_enum = old.get("enum")
    new_enum = new.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed = {repr(value) for value in old_enum} - {repr(value) for value in new_enum}
        if removed:
            issues.append(f"{location}: removed enum values " + ", ".join(sorted(removed)))
    elif old_enum is None and isinstance(new_enum, list):
        issues.append(f"{location}: added enum restriction")

    if "const" in new and old.get("const") != new.get("const"):
        issues.append(f"{location}: changed const")

    old_properties = _mapping(old.get("properties"), location=f"{location}.properties")
    new_properties = _mapping(new.get("properties"), location=f"{location}.properties")
    for property_name in sorted(old_properties.keys() - new_properties.keys()):
        issues.append(f"{location}: removed property {property_name}")

    old_required_value = old.get("required", [])
    new_required_value = new.get("required", [])
    if not isinstance(old_required_value, list) or not all(
        isinstance(value, str) for value in old_required_value
    ):
        raise OpenAPIContractError(f"{location}.required must be an array of strings")
    if not isinstance(new_required_value, list) or not all(
        isinstance(value, str) for value in new_required_value
    ):
        raise OpenAPIContractError(f"{location}.required must be an array of strings")
    for property_name in sorted(set(new_required_value) - set(old_required_value)):
        issues.append(f"{location}: added required property {property_name}")

    for property_name in sorted(old_properties.keys() & new_properties.keys()):
        issues.extend(
            _schema_compatibility_issues_at(
                f"{location}.{property_name}",
                old_properties[property_name],
                new_properties[property_name],
            )
        )

    if "items" in old or "items" in new:
        if "items" not in old or "items" not in new:
            issues.append(f"{location}: changed array item contract")
        else:
            issues.extend(
                _schema_compatibility_issues_at(
                    f"{location}.items",
                    old["items"],
                    new["items"],
                )
            )

    old_additional = old.get("additionalProperties", True)
    new_additional = new.get("additionalProperties", True)
    if old_additional is not False and new_additional is False:
        issues.append(f"{location}: disallowed additional properties")
    elif isinstance(old_additional, dict) and isinstance(new_additional, dict):
        issues.extend(
            _schema_compatibility_issues_at(
                f"{location}.additionalProperties",
                old_additional,
                new_additional,
            )
        )

    for keyword in ("allOf", "anyOf", "oneOf", "not"):
        if old.get(keyword) != new.get(keyword):
            issues.append(f"{location}: changed {keyword}")
    issues.extend(_tightened_constraint_issues(location, old, new))
    return issues


def _content_compatibility_issues(
    location: str,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    issues: list[str] = []
    old_content = _mapping(previous.get("content"), location=f"{location}.content")
    new_content = _mapping(current.get("content"), location=f"{location}.content")
    for media_type in sorted(old_content.keys() - new_content.keys()):
        issues.append(f"{location}: removed media type {media_type}")
    for media_type in sorted(old_content.keys() & new_content.keys()):
        old_media = _mapping(old_content[media_type], location=f"{location}.{media_type}")
        new_media = _mapping(new_content[media_type], location=f"{location}.{media_type}")
        if "schema" in old_media or "schema" in new_media:
            if "schema" not in old_media or "schema" not in new_media:
                issues.append(f"{location} {media_type}: changed schema presence")
            else:
                issues.extend(
                    _schema_compatibility_issues_at(
                        f"{location} {media_type} schema",
                        old_media["schema"],
                        new_media["schema"],
                    )
                )
    return issues


def _parameter_compatibility_issues(
    location: str,
    previous: Any,
    current: Any,
) -> list[str]:
    old = _mapping(previous, location=location)
    new = _mapping(current, location=location)
    issues = _reference_compatibility_issues(location, old, new)
    if issues:
        return issues
    if new.get("required") is True and old.get("required") is not True:
        issues.append(f"{location}: made parameter required")
    if "schema" in old or "schema" in new:
        if "schema" not in old or "schema" not in new:
            issues.append(f"{location}: changed parameter schema presence")
        else:
            issues.extend(
                _schema_compatibility_issues_at(
                    f"{location} schema",
                    old["schema"],
                    new["schema"],
                )
            )
    issues.extend(_content_compatibility_issues(location, old, new))
    return issues


def _request_body_compatibility_issues(
    location: str,
    previous: Any,
    current: Any,
) -> list[str]:
    old = _mapping(previous, location=location)
    new = _mapping(current, location=location)
    issues = _reference_compatibility_issues(location, old, new)
    if issues:
        return issues
    if new.get("required") is True and old.get("required") is not True:
        issues.append(f"{location}: made request body required")
    issues.extend(_content_compatibility_issues(location, old, new))
    return issues


def _response_compatibility_issues(
    location: str,
    previous: Any,
    current: Any,
) -> list[str]:
    old = _mapping(previous, location=location)
    new = _mapping(current, location=location)
    issues = _reference_compatibility_issues(location, old, new)
    if issues:
        return issues
    old_headers = _mapping(old.get("headers"), location=f"{location}.headers")
    new_headers = _mapping(new.get("headers"), location=f"{location}.headers")
    for header in sorted(old_headers.keys() - new_headers.keys()):
        issues.append(f"{location}: removed response header {header}")
    issues.extend(_content_compatibility_issues(location, old, new))
    return issues


def _operation_compatibility_issues(
    location: str,
    previous: Any,
    current: Any,
) -> list[str]:
    old = _mapping(previous, location=location)
    new = _mapping(current, location=location)
    issues: list[str] = []
    old_operation_id = old.get("operationId")
    new_operation_id = new.get("operationId")
    if old_operation_id is not None and old_operation_id != new_operation_id:
        issues.append(
            f"{location}: changed operationId from {old_operation_id!r} to {new_operation_id!r}"
        )
    issues.extend(_security_compatibility_issues(location, old, new))
    old_responses = _mapping(old.get("responses"), location=f"{location}.responses")
    new_responses = _mapping(new.get("responses"), location=f"{location}.responses")
    for status in sorted(old_responses.keys() - new_responses.keys()):
        issues.append(f"{location}: removed response {status}")
    for status in sorted(old_responses.keys() & new_responses.keys()):
        issues.extend(
            _response_compatibility_issues(
                f"{location} response {status}",
                old_responses[status],
                new_responses[status],
            )
        )

    old_parameters_value = old.get("parameters", [])
    new_parameters_value = new.get("parameters", [])
    if not isinstance(old_parameters_value, list) or not isinstance(new_parameters_value, list):
        raise OpenAPIContractError(f"{location}.parameters must be an array")
    old_parameters = {
        key: parameter
        for parameter in old_parameters_value
        if (key := _parameter_key(parameter)) is not None
    }
    new_parameters = {
        key: parameter
        for parameter in new_parameters_value
        if (key := _parameter_key(parameter)) is not None
    }
    for key in sorted(old_parameters.keys() - new_parameters.keys()):
        issues.append(f"{location}: removed accepted {key[0]} parameter {key[1]}")
    for key in sorted(new_parameters.keys() - old_parameters.keys()):
        parameter = new_parameters[key]
        if isinstance(parameter, dict) and parameter.get("required") is True:
            issues.append(f"{location}: added required {key[0]} parameter {key[1]}")
    for key in sorted(old_parameters.keys() & new_parameters.keys()):
        issues.extend(
            _parameter_compatibility_issues(
                f"{location} {key[0]} parameter {key[1]}",
                old_parameters[key],
                new_parameters[key],
            )
        )

    old_body = old.get("requestBody")
    new_body = new.get("requestBody")
    if isinstance(old_body, dict) and not isinstance(new_body, dict):
        issues.append(f"{location}: removed request body support")
    if (
        isinstance(new_body, dict)
        and new_body.get("required") is True
        and (not isinstance(old_body, dict) or old_body.get("required") is not True)
    ):
        issues.append(f"{location}: made request body required")
    if isinstance(old_body, dict) and isinstance(new_body, dict):
        issues.extend(
            _request_body_compatibility_issues(f"{location} request body", old_body, new_body)
        )
    return issues


def _security_requirement_is_no_stricter(
    current: Mapping[str, list[str]],
    previous: Mapping[str, list[str]],
) -> bool:
    """Return whether credentials satisfying ``previous`` also satisfy ``current``."""

    if not set(current).issubset(previous):
        return False
    return all(set(current[scheme]).issubset(previous[scheme]) for scheme in current)


def _security_compatibility_issues(
    location: str,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    old_security = _security_requirements(
        previous.get("security", []),
        location=f"{location}.security",
    )
    new_security = _security_requirements(
        current.get("security", []),
        location=f"{location}.security",
    )
    if not old_security or not new_security:
        return [] if not new_security else [f"{location}: added authentication requirement"]
    for old_requirement in old_security:
        if not any(
            _security_requirement_is_no_stricter(new_requirement, old_requirement)
            for new_requirement in new_security
        ):
            return [f"{location}: tightened authentication requirements"]
    return []


def _schema_compatibility_issues(name: str, previous: Any, current: Any) -> list[str]:
    return _schema_compatibility_issues_at(f"schema {name}", previous, current)


def compatibility_issues(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    old_paths = _mapping(previous.get("paths"), location="previous.paths")
    new_paths = _mapping(current.get("paths"), location="current.paths")
    for route in sorted(old_paths.keys() - new_paths.keys()):
        issues.append(f"removed path {route}")
    for route in sorted(old_paths.keys() & new_paths.keys()):
        old_operations = _operations(old_paths[route])
        new_operations = _operations(new_paths[route])
        for method in sorted(old_operations.keys() - new_operations.keys()):
            issues.append(f"{method.upper()} {route}: removed operation")
        for method in sorted(old_operations.keys() & new_operations.keys()):
            issues.extend(
                _operation_compatibility_issues(
                    f"{method.upper()} {route}",
                    _effective_operation(old_paths[route], old_operations[method]),
                    _effective_operation(new_paths[route], new_operations[method]),
                )
            )

    old_components = _mapping(previous.get("components"), location="previous.components")
    new_components = _mapping(current.get("components"), location="current.components")
    for section_name in sorted(old_components):
        old_section = _mapping(
            old_components[section_name],
            location=f"previous.components.{section_name}",
        )
        new_section = _mapping(
            new_components.get(section_name),
            location=f"current.components.{section_name}",
        )
        for component_name in sorted(old_section.keys() - new_section.keys()):
            noun = "schema" if section_name == "schemas" else "component"
            issues.append(f"removed {noun} {component_name}")
        if section_name not in new_components:
            continue
        if section_name == "schemas":
            for component_name in sorted(old_section.keys() & new_section.keys()):
                issues.extend(
                    _schema_compatibility_issues(
                        component_name,
                        old_section[component_name],
                        new_section[component_name],
                    )
                )
        elif section_name == "parameters":
            for component_name in sorted(old_section.keys() & new_section.keys()):
                issues.extend(
                    _parameter_compatibility_issues(
                        f"component parameter {component_name}",
                        old_section[component_name],
                        new_section[component_name],
                    )
                )
        elif section_name == "requestBodies":
            for component_name in sorted(old_section.keys() & new_section.keys()):
                issues.extend(
                    _request_body_compatibility_issues(
                        f"component request body {component_name}",
                        old_section[component_name],
                        new_section[component_name],
                    )
                )
        elif section_name == "responses":
            for component_name in sorted(old_section.keys() & new_section.keys()):
                issues.extend(
                    _response_compatibility_issues(
                        f"component response {component_name}",
                        old_section[component_name],
                        new_section[component_name],
                    )
                )
        else:
            for component_name in sorted(old_section.keys() & new_section.keys()):
                if old_section[component_name] != new_section[component_name]:
                    issues.append(f"component {section_name}.{component_name}: changed contract")
    return tuple(issues)


def check_compatibility(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    issues = compatibility_issues(previous, current)
    if issues:
        raise IncompatibleOpenAPIError(issues)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate and verify OpenAPI fragments")
    parser.add_argument("--fragments", type=Path, default=Path("openapi"))
    parser.add_argument("--output", type=Path, default=Path("openapi.generated.yaml"))
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="explicitly render the production-composed app.openapi() document (the default)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed output differs; do not write it",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="fail if the aggregate is incompatible with this prior aggregate",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    try:
        formal = aggregate_fragments(args.fragments)
        if args.runtime:
            document = runtime_document()
            check_formal_runtime_contract(formal, document)
        else:
            document = formal
        rendered = render(document)
        if args.baseline is not None:
            if not args.baseline.is_file():
                raise OpenAPIContractError(f"OpenAPI baseline does not exist: {args.baseline}")
            check_compatibility(_load_yaml(args.baseline), document)
        if args.check:
            if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
                raise OpenAPIContractError(
                    f"{args.output} is stale; run hc-openapi and commit the result"
                )
        else:
            args.output.write_text(rendered, encoding="utf-8", newline="\n")
    except OpenAPIContractError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
