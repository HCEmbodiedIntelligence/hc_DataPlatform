"""Replay C's processor after B publication while withholding C's saved receipt."""
import json
import os
from pathlib import Path

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.robot_ingest.lerobot_processor import NativeLeRobotProcessor
from hc_data_platform.robot_ingest.models import RobotIngestUpload
from hc_data_platform.robot_ingest.processing_contract import CommittedSource, SourceEpisode
from hc_data_platform.runtime import build_runtime

ORG, PROJECT, REGION = "openarm-e-synthetic", "openarm-e-project", "openarm-e-region"
RAW = "raw-2b5124d4bc445d5daf376da70acbeb98"


def counts(connection):
    tables = {
        "native_jobs": "workflow.jobs",
        "lineage": "lance_rollout_lineage",
        "qc": "qc_reports",
        "annotations": "annotation.annotation_tasks",
        "media": "aligned_media.artifacts",
    }
    return {key: connection.execute(
        f"SELECT count(*) FROM {table} WHERE project_id=%s",
        (PROJECT,),
    ).fetchone()[0] for key, table in tables.items()}


def main():
    token = bind_request_context(RequestContext(
        organization_id=ORG, project_id=PROJECT, region_code=REGION,
        subject_id="openarm-e-recovery-verifier", service_identity=True,
    ))
    try:
        runtime = build_runtime(get_settings(), include_media=True)
        pipeline = runtime.activities.lerobot_pipeline
        with pipeline.connections() as connection:
            row = connection.execute(
                "SELECT upload_document FROM ingest.robot_ingest_uploads "
                "WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s",
                (ORG, PROJECT, REGION, RAW),
            ).fetchone()
            assert row is not None
            upload = RobotIngestUpload.model_validate(row[0])
            before = counts(connection)
        task, document = runtime.robot_processing.read(upload)
        assert task and document.phase == "DONE" and len(document.episodes) == 2
        processor = NativeLeRobotProcessor(pipeline, None, None, task_queue="unused-on-receipt-recovery")
        recovered = []
        for episode in document.episodes:
            receipt = processor.process(
                CommittedSource(upload, pipeline.storage),
                SourceEpisode(source_episode_id=episode.source_episode_id,
                              source_episode_index=episode.source_episode_index),
                episode_id=episode.episode_id,
                attempt_id="new-attempt-after-lost-c-receipt",
            )
            assert receipt == episode.receipt
            recovered.append(receipt.model_dump())
        with pipeline.connections() as connection:
            after = counts(connection)
        assert after == before, (before, after)
        output = {"case": "native publication succeeded but C receipt lost",
                  "source": RAW, "new_attempt_id": "new-attempt-after-lost-c-receipt",
                  "recovered_receipts": recovered, "business_counts_before": before,
                  "business_counts_after": after, "new_native_jobs": 0}
        Path(os.environ["E_RECOVERY_OUTPUT"]).write_text(json.dumps(output, indent=2) + "\n")
        print(json.dumps(output))
    finally:
        reset_request_context(token)


if __name__ == "__main__":
    main()
