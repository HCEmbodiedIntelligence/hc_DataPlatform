from .captures import capture_counts
from .models import ProgressFacts


def combine_capture_facts(legacy, captures):
    received = [item for item in captures if item.received]
    known = [item.duration_seconds for item in received if item.duration_seconds is not None]
    unknown = legacy.duration_unknown_package_count + len(received) - len(known)
    facts = legacy.model_dump()
    facts.update(
        received_package_count=legacy.received_package_count + len(received),
        pass_count=legacy.pass_count + sum(item.qc_outcome == "PASS" for item in received),
        risk_count=legacy.risk_count + sum(item.qc_outcome == "RISK" for item in received),
        reject_count=legacy.reject_count + sum(item.qc_outcome == "REJECT" for item in received),
        captured_duration_seconds=None
        if unknown
        else (legacy.captured_duration_seconds or 0) + sum(known),
        duration_observed_package_count=legacy.duration_observed_package_count + len(known),
        duration_unknown_package_count=unknown,
        device_ids=tuple(sorted(set(legacy.device_ids) | {item.robot_id for item in received})),
        captures=capture_counts(captures),
    )
    return ProgressFacts.model_validate(facts)
