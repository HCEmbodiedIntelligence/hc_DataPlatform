import pytest

from hc_data_platform.capabilities.registry import (
    CapabilityDescriptor,
    ImplementationRef,
    ImplementationUnavailable,
    UnsupportedInputs,
    VersionedRegistry,
)


def descriptor(version: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        ImplementationRef("test_format", version), frozenset({"samples/v1"})
    )


def test_frozen_selection_never_falls_forward_to_a_new_default() -> None:
    registry: VersionedRegistry[str] = VersionedRegistry()
    registry.register(descriptor("1"), "old-reader")
    registry.register(descriptor("2"), "new-reader", default=True)
    inputs = frozenset({"samples/v1"})
    assert registry.select("test_format", available_inputs=inputs).implementation == "new-reader"
    frozen = ImplementationRef("test_format", "1")
    assert (
        registry.select(frozen.key, version=frozen.version, available_inputs=inputs).implementation
        == "old-reader"
    )
    with pytest.raises(ImplementationUnavailable):
        registry.select("test_format", version="missing", available_inputs=inputs)


def test_duplicate_and_ambiguous_registration_leave_existing_routes_intact() -> None:
    registry: VersionedRegistry[str] = VersionedRegistry()
    registry.register(descriptor("1"), "first", default=True)
    with pytest.raises(ValueError, match="duplicate implementation"):
        registry.register(descriptor("1"), "replacement")
    with pytest.raises(ValueError, match="duplicate default"):
        registry.register(descriptor("2"), "second", default=True)
    assert registry.descriptors() == (descriptor("1"),)


def test_missing_capabilities_are_explicit_and_registration_requires_no_domains() -> None:
    registry: VersionedRegistry[str] = VersionedRegistry()
    registry.register(descriptor("1"), "reader", default=True)
    with pytest.raises(UnsupportedInputs) as captured:
        registry.select("test_format", available_inputs=frozenset({"different/v1"}))
    assert captured.value.missing == frozenset({"samples/v1"})


def test_no_default_is_not_inferred_from_registration_order() -> None:
    registry: VersionedRegistry[str] = VersionedRegistry()
    registry.register(descriptor("1"), "reader")
    with pytest.raises(ImplementationUnavailable):
        registry.select("test_format", available_inputs=frozenset({"samples/v1"}))
