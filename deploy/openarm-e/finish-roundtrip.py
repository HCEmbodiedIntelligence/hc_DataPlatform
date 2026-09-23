"""Review and export the real robot ingest result in the isolated E deployment."""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import replace
from pathlib import Path
from urllib.request import Request, urlopen

from hc_data_platform.annotation.models import AnnotationStatus, AnnotationTag, ReviewDecision
from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository
from hc_data_platform.lerobot_imports.committed import discover_committed_source, read_committed_manifest
from hc_data_platform.publishing.models import ExportFormat, PublishDatasetRequestV1
from hc_data_platform.runtime import build_runtime
from hc_data_platform.security.auth import AuthContext

ORG, PROJECT, REGION = "openarm-e-synthetic", "openarm-e-project", "openarm-e-region"
RAW = "raw-2b5124d4bc445d5daf376da70acbeb98"
DATASET = "dataset_openarm_e_synthetic"


def main() -> None:
    actor = AuthContext(
        subject_id="openarm-e-operator", organization_ids=frozenset({ORG}),
        project_ids=frozenset({PROJECT}), region_codes=frozenset({REGION}),
        organization_scope_triples=frozenset({(ORG, PROJECT, REGION), (ORG, PROJECT, None)}),
        scope_pairs=frozenset({(PROJECT, REGION), (PROJECT, None)}),
        capabilities=frozenset({"annotation_task.claim", "annotation.read", "annotation.write",
                               "annotation.save", "annotation.submit", "annotation.review",
                               "dataset_version.publish"}),
    )
    token = bind_request_context(RequestContext(
        organization_id=ORG, project_id=PROJECT, region_code=REGION,
        subject_id=actor.subject_id, request_id="openarm-e-real-d-review"))
    try:
        runtime = build_runtime(get_settings(), include_media=True)
        pipeline = runtime.activities.lerobot_pipeline
        raw_repo = PostgresRawSourceRepository(pipeline.connections)
        raw = raw_repo.get_source(organization_id=ORG, project_id=PROJECT,
                                  region_code=REGION, raw_source_id=RAW)
        assert raw is not None and raw.raw_status.value == "COMMITTED"
        discovery = discover_committed_source(pipeline.storage, raw)
        episodes = raw_repo.list_episodes(organization_id=ORG, project_id=PROJECT,
                                           region_code=REGION, raw_source_id=RAW)
        assert len(episodes) == len(discovery.episodes) == 2
        assert all(ep.status.value == "READY" and
                   ep.dataset_version and ep.lance_version for ep in episodes)
        manifest, original_body = read_committed_manifest(pipeline.storage, raw)
        assert manifest['schema_version'] == 'raw-upload-manifest/v1'
        assert json.loads(original_body)['schema_version'] == 'robot-ingest-committed/v1'
        asset_hashes = {}
        for asset in manifest['files']:
            body = b''.join(pipeline.storage.read_chunks(asset['object_key']))
            digest = hashlib.sha256(body).hexdigest()
            assert len(body) == asset['size'] and digest == asset['sha256']
            asset_hashes[asset['path']] = digest
        qc = []
        with pipeline.connections() as connection:
            for ep, source in zip(episodes, discovery.episodes, strict=True):
                assert ep.source_episode_index == source['episode_index']
                report = runtime.quality_repository.get_report(
                    project_id=PROJECT, region_code=REGION, rollout_id=ep.episode_id)
                assert report is not None and report.status.value == 'PASS'
                window = runtime.catalog.read_steps(
                    DATASET, ep.episode_id, 0, ep.frame_count,
                    project_id=PROJECT, version=ep.dataset_version)
                assert len(window.steps) == ep.frame_count == source['length']
                row = connection.execute(
                    "SELECT task_id FROM annotation.annotation_tasks "
                    "WHERE organization_id=%s AND rollout_id=%s", (ORG, ep.episode_id)).fetchone()
                assert row is not None
                annotation_id = row[0]
                task = runtime.annotation.get_task(annotation_id)
                if task.status is not AnnotationStatus.APPROVED:
                    task = runtime.annotation.claim(annotation_id, actor)
                    revision = runtime.annotation.save_draft(
                        annotation_id, actor, [],
                        tags=[AnnotationTag(annotation_id='outcome', tag_id=source['outcome'],
                                            path=(source['outcome'],), start_step=0,
                                            end_step=ep.frame_count)],
                        expected_revision=0, if_match=task.etag,
                        client_mutation_id='e-outcome-'+str(ep.source_episode_index))
                    submitted = runtime.annotation.submit(
                        annotation_id, actor, expected_revision=revision.revision,
                        if_match=runtime.annotation.get_task(annotation_id).etag)
                    runtime.annotation.review(
                        annotation_id, replace(actor,subject_id='openarm-e-reviewer'),
                        ReviewDecision.APPROVE, revision=revision.revision,
                        if_match=submitted.etag)
                qc.append({'episode_id':ep.episode_id, 'source_episode_id':source['source_episode_id'],
                           'frames':ep.frame_count, 'status':report.status.value,
                           'qc_sha256':report.content_sha256,
                           'dataset_version':ep.dataset_version, 'lance_version':ep.lance_version,
                           'annotation_task_id':annotation_id})
        version = max(ep.dataset_version for ep in episodes)
        published = runtime.publisher.publish(PublishDatasetRequestV1(
            project_id=PROJECT, dataset_id=DATASET,
            dataset_version='openarm-e-real-v1', base_lance_version=str(version)))
        assert len(published.rollouts) == 2
        exported = runtime.exporter.export(published, format=ExportFormat.LEROBOT_V3)
        download=runtime.exporter.authorize_download(exported)
        with urlopen(Request(download,headers={'Range':'bytes=0-31'})) as stream:
            assert stream.status==206
            first_32=stream.read()
            assert len(first_32)==32
        sink=runtime.exporter._sink
        head_url=sink._presign_client.generate_presigned_url(
            'head_object',Params={'Bucket':sink._bucket,
                                  'Key':sink._key(exported.artifact_uri)},
            ExpiresIn=60,HttpMethod='HEAD')
        with urlopen(Request(head_url,method='HEAD')) as stream:
            assert stream.status==200
            head_length=int(stream.headers['Content-Length'])
        with urlopen(download) as stream:
            content = stream.read()
        assert head_length==len(content) and content[:32]==first_32
        digest = hashlib.sha256(content).hexdigest()
        assert digest == exported.artifact_content_hash
        destination=Path(os.environ['E_ROUNDTRIP_OUTPUT'])
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'a-actual.zip').write_bytes(content)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            archive.extractall(destination/'a-actual')
            annotations=json.loads(archive.read('meta/annotations.json'))
            assert [x['source_episode']['source_episode_id'] for x in annotations['episodes']] == [
                x['source_episode_id'] for x in discovery.episodes]
        evidence={
            'source':'actual A exporter via D real robot credential HTTP and E real Worker',
            'raw_source_id':RAW, 'immutable_robot_manifest_sha256':hashlib.sha256(original_body).hexdigest(),
            'asset_sha256':asset_hashes, 'qc_and_review':qc,
            'published_version':'openarm-e-real-v1', 'export_sha256':digest,
            'gateway_signed_range_get':206,'gateway_signed_head':200,
            'export_path':str(destination/'a-actual'),
        }
        (destination/'verification.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps({'raw_source_id':RAW, 'episodes':len(qc),'assets':len(asset_hashes),
                          'export_sha256':digest}))
    finally:
        reset_request_context(token)


if __name__ == '__main__':
    main()
