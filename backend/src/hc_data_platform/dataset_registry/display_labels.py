"""Scoped, bounded display-name lookup. Stored identities are never changed."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import DatasetPageScope

LabelKind = Literal["dataset", "collection_task", "robot", "episode", "annotation_task", "rollout"]
READ_CAPABILITIES = {
    "dataset": "dataset.read",
    "collection_task": "upload.read",
    "robot": "robot.read",
    "episode": "episode.read",
    "annotation_task": "annotation_task.read",
    "rollout": "upload.read",
}
Identifier = Annotated[str, Field(min_length=1, max_length=256)]


class DisplayReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: LabelKind
    id: Identifier
    dataset_id: Identifier | None = None

    @model_validator(mode="after")
    def episode_has_dataset(self):
        if self.kind == "episode" and not self.dataset_id:
            raise ValueError("Episode labels require their Dataset identity")
        return self


class DisplayLabelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    references: tuple[DisplayReference, ...] = Field(min_length=1, max_length=100)


class DisplayLabel(DisplayReference):
    label: str
    description_label: str | None = None


class DisplayLabelResponse(BaseModel):
    items: tuple[DisplayLabel, ...]


DisplayLabelReader = Callable[
    [DatasetPageScope, tuple[DisplayReference, ...]], tuple[DisplayLabel, ...]
]


class PostgresDisplayLabels:
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    def __call__(self, scope, references):
        labels: dict[tuple[str, str, str | None], tuple[str, str | None]] = {}
        connection = self._connection_factory()
        params = (scope.organization_id, scope.project_id, scope.region_code)
        try:
            with connection.cursor() as cursor:
                for kind in dict.fromkeys(ref.kind for ref in references):
                    refs = tuple(ref for ref in references if ref.kind == kind)
                    ids = list(dict.fromkeys(ref.id for ref in refs))
                    if kind == "dataset":
                        cursor.execute(
                            """SELECT dataset_id,name,collection_task_id
                            FROM dataset_registry.datasets
                            WHERE organization_id=%s AND project_id=%s AND region_code=%s
                              AND dataset_id=ANY(%s)""",
                            (*params, ids),
                        )
                        from hc_data_platform.collection_tasks.models import (
                            dataset_id_for_collection_task,
                        )

                        for identity, name, task in cursor.fetchall():
                            task_owned = task and identity == dataset_id_for_collection_task(
                                scope.organization_id, scope.project_id, task
                            )
                            labels[(kind, identity, None)] = (
                                name,
                                "任务描述（创建时同步）" if task_owned else "数据集说明",
                            )
                    elif kind == "collection_task":
                        cursor.execute(
                            """SELECT collection_task_id,task_code
                            FROM collection_tasks.collection_tasks
                            WHERE organization_id=%s AND project_id=%s
                              AND collection_task_id=ANY(%s)""",
                            (*params[:2], ids),
                        )
                        for identity, code in cursor.fetchall():
                            labels[(kind, identity, None)] = (
                                str(int(code)) if code.isdecimal() else code,
                                None,
                            )
                    elif kind == "robot":
                        cursor.execute(
                            """SELECT robot_id,display_name FROM robotics.robot_assets
                            WHERE organization_id=%s AND robot_id=ANY(%s)""",
                            (params[0], ids),
                        )
                        for identity, name in cursor.fetchall():
                            labels[(kind, identity, None)] = (name, None)
                    elif kind == "episode":
                        cursor.execute(
                            """SELECT DISTINCT ON (e.dataset_id,e.episode_id)
                              e.dataset_id,e.episode_id,e.ordinal
                            FROM dataset_registry.dataset_version_episodes e
                            JOIN dataset_registry.dataset_versions v
                              USING (organization_id,project_id,region_code,dataset_id,version_id)
                            JOIN unnest(%s::text[],%s::text[]) wanted(dataset_id,episode_id)
                              ON wanted.dataset_id=e.dataset_id AND wanted.episode_id=e.episode_id
                            WHERE e.organization_id=%s AND e.project_id=%s AND e.region_code=%s
                            ORDER BY e.dataset_id,e.episode_id,v.created_at DESC,
                                     v.version_id DESC""",
                            ([ref.dataset_id for ref in refs], [ref.id for ref in refs], *params),
                        )
                        for dataset, identity, ordinal in cursor.fetchall():
                            labels[(kind, identity, dataset)] = (f"Episode {ordinal + 1}", None)
                    elif kind == "annotation_task":
                        cursor.execute(
                            """SELECT a.task_id,e.ordinal,
                              COALESCE(episode_task.task_code,dataset_task.task_code)
                            FROM annotation.annotation_tasks a
                            LEFT JOIN LATERAL (
                              SELECT e.ordinal,e.task
                              FROM dataset_registry.dataset_version_episodes e
                              JOIN dataset_registry.dataset_versions v
                                USING (organization_id,project_id,region_code,dataset_id,version_id)
                              WHERE e.organization_id=a.organization_id
                                AND e.project_id=a.project_id
                                AND e.region_code=a.region_code AND e.dataset_id=a.dataset_id
                                AND (e.episode_id=a.data_ref #>> '{identity,episode_id}'
                                  OR e.episode_document ->> 'rollout_id'=a.rollout_id)
                              ORDER BY v.created_at DESC,v.version_id DESC LIMIT 1
                            ) e ON true
                            LEFT JOIN dataset_registry.datasets d
                              ON d.organization_id=a.organization_id AND d.project_id=a.project_id
                             AND d.region_code=a.region_code AND d.dataset_id=a.dataset_id
                            LEFT JOIN collection_tasks.collection_tasks episode_task
                              ON episode_task.organization_id=a.organization_id
                             AND episode_task.project_id=a.project_id
                             AND episode_task.collection_task_id=e.task
                            LEFT JOIN collection_tasks.collection_tasks dataset_task
                              ON dataset_task.organization_id=a.organization_id
                             AND dataset_task.project_id=a.project_id
                             AND dataset_task.collection_task_id=d.collection_task_id
                            WHERE a.organization_id=%s AND a.project_id=%s AND a.region_code=%s
                              AND a.task_id=ANY(%s)""",
                            (*params, ids),
                        )
                        for identity, ordinal, task_code in cursor.fetchall():
                            episode = (
                                f"Episode {ordinal + 1}" if ordinal is not None else "待关联片段"
                            )
                            task = (
                                f"任务 {int(task_code) if task_code.isdecimal() else task_code}"
                                if task_code
                                else "未关联采集任务"
                            )
                            labels[(kind, identity, None)] = (f"{task} · {episode}", None)
                    elif kind == "rollout":
                        cursor.execute(
                            """SELECT recording_document FROM ingest.continuous_recordings
                            WHERE organization_id=%s AND project_id=%s AND region_code=%s
                              AND (rollout_id=ANY(%s) OR recording_id=ANY(%s))""",
                            (*params, ids, ids),
                        )
                        from hc_data_platform.continuous_recordings.models import (
                            ContinuousRecording,
                        )
                        from hc_data_platform.continuous_recordings.presentation import (
                            present_recordings,
                        )

                        recordings = tuple(
                            ContinuousRecording.model_validate(row[0]) for row in cursor.fetchall()
                        )
                        for recording in present_recordings(self._connection_factory, recordings):
                            labels[(kind, recording.rollout_id, None)] = (recording.file_name, None)
                            labels[(kind, recording.recording_id, None)] = (
                                recording.file_name,
                                None,
                            )
        finally:
            connection.close()
        return tuple(
            DisplayLabel(**ref.model_dump(), label=labels[key][0], description_label=labels[key][1])
            for ref in dict.fromkeys(references)
            if (key := (ref.kind, ref.id, ref.dataset_id if ref.kind == "episode" else None))
            in labels
        )
