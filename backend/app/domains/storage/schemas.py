"""Strict request schemas from the frozen P12/P13 storage contract."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Id = Annotated[str, StringConstraints(min_length=1, max_length=160)]
StorageObjectRole = Literal["SOURCE", "DERIVED", "PREVIEW", "EXPORT", "ROBOT_ASSET"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InventoryRefreshRequest(StrictModel):
    expected_snapshot_id: str | None
    reason: Literal["USER_REFRESH"]


class StorageObjectsTarget(StrictModel):
    target_kind: Literal["STORAGE_OBJECTS"]
    scope_id: Id
    object_role: StorageObjectRole
    schedule_window_id: Id


class IncompleteMultipartTarget(StrictModel):
    target_kind: Literal["INCOMPLETE_MULTIPART"]
    scope_id: Id
    object_role: None
    schedule_window_id: Id


LifecycleTarget = Annotated[
    StorageObjectsTarget | IncompleteMultipartTarget,
    Field(discriminator="target_kind"),
]


class TransitionAction(StrictModel):
    type: Literal["TRANSITION"]
    after_days: int = Field(ge=1, le=36500)
    target_class: Literal["IA", "ARCHIVE"]


class AbortMultipartAction(StrictModel):
    type: Literal["ABORT_MULTIPART"]
    after_days: int = Field(ge=1, le=36500)


class DeleteObjectAction(StrictModel):
    type: Literal["DELETE_OBJECT"]
    after_days: int = Field(ge=1, le=36500)


LifecycleAction = Annotated[
    TransitionAction | AbortMultipartAction | DeleteObjectAction,
    Field(discriminator="type"),
]


class PolicyInput(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(max_length=512)
    target: LifecycleTarget
    age_basis: Literal["CREATED_AT", "LAST_ACCESSED_AT", "EXPIRES_AT", "LAST_ACTIVITY_AT"]
    actions: list[LifecycleAction] = Field(min_length=1)
    exclusion_scope_ids: list[Id]
    schedule_window_id: Id

    @model_validator(mode="after")
    def validate_target_action_invariant(self) -> PolicyInput:
        action_types = {action.type for action in self.actions}
        if self.target.target_kind == "INCOMPLETE_MULTIPART" and action_types != {
            "ABORT_MULTIPART"
        }:
            raise ValueError("INCOMPLETE_MULTIPART accepts only ABORT_MULTIPART")
        if (
            self.target.target_kind == "STORAGE_OBJECTS"
            and self.target.object_role == "SOURCE"
            and "DELETE_OBJECT" in action_types
        ):
            raise ValueError("SOURCE objects cannot be deletion targets")
        if self.target.schedule_window_id != self.schedule_window_id:
            raise ValueError("target and policy schedule_window_id must match")
        return self


class CreateLifecyclePolicyRequest(StrictModel):
    policy: PolicyInput
    expected_policy_set_version: Id


class UpdateLifecyclePolicyRequest(StrictModel):
    policy: PolicyInput
    expected_policy_version: Id
    expected_policy_set_version: Id


class PolicyVersionRef(StrictModel):
    policy_id: Id
    version: Id


class EnableConfirmation(StrictModel):
    impact_digest: Id
    acknowledged_risks: list[
        Literal[
            "TRANSITION_COST",
            "RESTORE_DELAY",
            "IRREVERSIBLE_DELETE",
            "IRREVERSIBLE_ABORT",
        ]
    ]


class EnableLifecyclePolicyRequest(StrictModel):
    policy_version: Id
    simulation_id: Id
    input_hash: Id
    snapshot_id: Id
    policy_set_version: Id
    policy_versions: list[PolicyVersionRef]
    confirmation: EnableConfirmation


class PauseLifecyclePolicyRequest(StrictModel):
    policy_version: Id
    expected_policy_set_version: Id
    reason_code: Literal["OPERATOR_REQUEST", "COST_REVIEW", "COMPLIANCE_REVIEW", "INCIDENT"]
    reason_text: str | None = Field(max_length=512)


class AllEnabledSelection(StrictModel):
    kind: Literal["ALL_ENABLED"]
    policy_ids: list[Id] = Field(max_length=0)


class PolicyIdSelection(StrictModel):
    kind: Literal["POLICY_ID"]
    policy_id: Id


SimulationSelection = Annotated[
    AllEnabledSelection | PolicyIdSelection,
    Field(discriminator="kind"),
]


class SavedSimulationRequest(StrictModel):
    mode: Literal["SAVED_POLICIES"]
    selection: SimulationSelection
    policy_set_version: Id
    policy_versions: list[PolicyVersionRef]
    snapshot_id: Id
    input_hash: Id


class DraftSimulationRequest(StrictModel):
    mode: Literal["DRAFT_POLICY"]
    draft_policy: PolicyInput
    baseline_policy_set_version: Id
    snapshot_id: Id


CreateSimulationRequest = Annotated[
    SavedSimulationRequest | DraftSimulationRequest,
    Field(discriminator="mode"),
]


class RestoreTargetInput(StrictModel):
    resource_type: Literal["STORAGE_OBJECT", "DATASET_VERSION", "EXPORT_BATCH"]
    resource_id: Id


class RestorePreflightRequest(StrictModel):
    mode: Literal["PREFLIGHT"]
    target: RestoreTargetInput


class RestoreConfirmation(StrictModel):
    cost_and_expiry_acknowledged: Literal[True]


class RestoreCommitRequest(StrictModel):
    mode: Literal["COMMIT"]
    preflight_token: str = Field(min_length=1)
    quote_id: Id
    restore_method: Literal["STANDARD", "EXPEDITED"]
    expected_target_version: Id
    confirmation: RestoreConfirmation


RestoreRequest = Annotated[
    RestorePreflightRequest | RestoreCommitRequest,
    Field(discriminator="mode"),
]


class PlanMultipartAbortRequest(StrictModel):
    upload_version: Id
    observed_last_activity_at: datetime
    confirmation_context: Literal["LIFECYCLE_GOVERNANCE"]


class AbortConfirmation(StrictModel):
    uploaded_parts_will_be_unrecoverable: Literal[True]


class AbortMultipartRequest(StrictModel):
    plan_id: Id
    confirmation_token: str = Field(min_length=1)
    upload_version: Id
    observed_last_activity_at: datetime
    confirmation: AbortConfirmation
