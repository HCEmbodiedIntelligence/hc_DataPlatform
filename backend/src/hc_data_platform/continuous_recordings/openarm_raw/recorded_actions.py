"""Read recorder-created next-state targets; never synthesize action on the platform."""
import math

from .alignment import target_time
from .primary_timeline import (PRIMARY_ACTION_POLICY, uses_primary_timeline,
                               consecutive_frames, next_observation, LEGACY_PRIMARY_ACTION_POLICY)

POLICY = "recorder-next-state/v1"


def apply_recorded_targets(row, targets, cfg):
    """Targets are supplied only for sources with no real samples in the recording."""
    for ident, data in targets.items():
        source = cfg["sources"][ident]
        axes = source["named_axes"]
        stamp = row.get("association_time_ns", row["target_time_ns"])
        next_grid = target_time(row["target_time_ns"], 1, cfg["dataset"]["fps"])
        # Grid rounding alternates by 1 ns at 30 Hz; preserve the recorded origin.
        gap = data.get("next_target_time_ns")
        gap_valid = type(gap) is int and abs(gap-next_grid) <= 1
        primary_mode = uses_primary_timeline(cfg['alignment'])
        frame_valid = (
            data.get('primary_frame') == row.get('primary_frame')
            and data.get('next_primary_frame') == row.get('next_primary_frame')
            and row.get('next_primary_frame') is not None
            and data.get('next_state_time_ns') == row['next_primary_frame']['capture_time_ns']
        ) if primary_mode else gap_valid
        valid_header = (
            data.get("schema_version") in ((PRIMARY_ACTION_POLICY, LEGACY_PRIMARY_ACTION_POLICY) if primary_mode else (POLICY,))
            and data.get("source_id") == ident
            and data.get("action_origin") == "next_state"
            and data.get("action_stage") == "derived"
            and data.get("selection") == "only_if_no_real_source_in_recording"
            and data.get("target_frame_index") == row["target_frame_index"]
            and data.get("target_time_ns") == row["target_time_ns"]
            and stamp is not None and data.get("capture_time_ns") == stamp
            and data.get("clock_epoch") == row["clock_epoch"]
            and data.get("next_frame_index") == row["target_frame_index"] + 1
            and frame_valid
            and type(data.get("next_state_time_ns")) is int
            and data["next_state_time_ns"] > stamp
            and data.get("joint_names") == [a["name"] for a in axes]
            and data.get("joint_units") == [a["unit"] for a in axes]
        )
        parts = data.get("components", {})
        shape_valid = all(isinstance(parts.get(key), list) and len(parts[key]) == len(axes)
                          for key in ("values", "valid", "source_ns", "evidence"))
        if not valid_header or not shape_valid:
            reason = ("next_frame_missing" if data.get("next_frame_index") is None
                      else "recorded_action_target_mismatch")
            row["invalid_reasons"].append(ident + ":" + reason)
            continue
        continuous = (not primary_mode or (next_observation(row.get('primary_frame'),
                       row.get('next_primary_frame'), cfg['alignment']) if data.get('schema_version') == PRIMARY_ACTION_POLICY
                       else consecutive_frames(row.get('primary_frame'), row.get('next_primary_frame'), cfg['dataset']['fps'])))
        if not continuous:
            row['invalid_reasons'].append(ident + ':next_primary_frame_discontinuous')
        accepted = [bool(continuous and flag is True and type(value) in (int, float) and math.isfinite(value))
                    for value, flag in zip(parts["values"], parts["valid"], strict=True)]
        row["action"][ident] = {
            "payload": {"position": parts["values"]},
            "components": {**parts, "valid": accepted},
            "action_origin": "next_state", "action_stage": "derived",
            "recorder_target": data,
            "action_dt_ns": data["next_state_time_ns"]-stamp,
        }
        # Remove only the missing-action errors resolved by this recorded value.
        resolved = {a["name"] + ":action_missing_invalid_or_expired"
                    for a, good in zip(axes, accepted, strict=True) if good}
        row["invalid_reasons"] = [r for r in row["invalid_reasons"]
                                  if r not in {ident + ":" + e for e in resolved}]
        if ident in row.get("source_issues", {}):
            row["source_issues"][ident] = [r for r in row["source_issues"][ident]
                                         if r not in resolved]
            if not row["source_issues"][ident]:
                del row["source_issues"][ident]
    row["valid"] = not row["invalid_reasons"]
    row["export_qualified"] = row["valid"] and cfg["alignment"]["strict"]
    row["strict_qualified"] = row["export_qualified"] and row["camera_validation"] == "strict"
