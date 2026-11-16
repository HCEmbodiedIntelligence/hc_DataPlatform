"""Expire superseded MCAP generations only after a verified replacement is usable."""

import json
import logging
import re
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.continuous_recordings.asset_models import RecordingAsset, RecordingUpload
from hc_data_platform.continuous_recordings.previews import preview_key
from hc_data_platform.core.context import current_request_context
from hc_data_platform.lerobot_imports.mcap_processing import lock_source
from hc_data_platform.object_storage.immutable import verify_object
from hc_data_platform.object_storage.providers import OssObjectStorage
from hc_data_platform.storage.cache_policy import select_policy
from hc_data_platform.storage.local_cache import CacheRoutingBucket
from hc_data_platform.storage.models import LifecyclePolicyAction

logger = logging.getLogger(__name__)
ACTION = LifecyclePolicyAction.CLEAN_SUPERSEDED_MCAP_BATCHES

# Partition by the frozen upload member, not a display name or a cross-upload checksum.
# The newest submission must itself be usable; a pending or failed retry cannot
# retire the last successful result. An old version or annotation is a real consumer.
CANDIDATES = """
WITH members AS (
  SELECT m.*, m.source_document->>'sha256' AS sha
  FROM ingest.mcap_processing_files m
  WHERE (organization_id,project_id,region_code)=(%s,%s,%s)
), ranked AS (
  SELECT m.*,first_value(processing_id) OVER (
    PARTITION BY raw_source_id,source_path,sha,collection_task_id,robot_id,dataset_id
    ORDER BY created_at DESC,processing_id
  ) AS latest FROM members m WHERE sha ~ '^[a-f0-9]{64}$'
)
SELECT old.raw_source_id, old.processing_id, old.recording_id, latest.processing_id,
       latest.recording_id, latest.source_document,
       greatest(latest.updated_at, r.updated_at, result.last_ready)
FROM ranked old JOIN members latest ON latest.processing_id=old.latest
JOIN ingest.continuous_recordings r ON
 (r.organization_id,r.project_id,r.region_code,r.recording_id)=
 (latest.organization_id,latest.project_id,latest.region_code,latest.recording_id)
LEFT JOIN LATERAL (
  SELECT bool_and(p.status IN ('READY','DISCARDED')) AS finished,
         bool_or(p.status='READY' AND p.data_ref IS NOT NULL) AS has_data,
         max(p.updated_at) AS last_ready
  FROM ingest.recording_episode_processing p
  WHERE (p.organization_id,p.project_id,p.region_code,p.recording_id,p.finalized_revision)=
        (r.organization_id,r.project_id,r.region_code,r.recording_id,r.finalized_revision)
) result ON true
LEFT JOIN ingest.mcap_batch_retirements retired ON
 (retired.organization_id,retired.project_id,retired.region_code,retired.processing_id)=
 (old.organization_id,old.project_id,old.region_code,old.processing_id)
WHERE old.processing_id<>latest.processing_id AND old.status IN ('READY','FAILED')
  AND latest.status='READY' AND retired.completed_at IS NULL
  AND (r.status='READY_FOR_SLICING' OR
       (r.status='SLICED' AND result.finished AND result.has_data))
  AND NOT EXISTS (
      SELECT FROM ingest.continuous_recordings prev
      WHERE (prev.organization_id,prev.project_id,prev.region_code,prev.recording_id)=
            (old.organization_id,old.project_id,old.region_code,old.recording_id)
        AND (prev.status IN ('PENDING_QC','QC_RUNNING') OR
             (prev.current_revision>0 AND prev.finalized_revision IS NULL) OR
             (prev.finalized_revision IS NOT NULL AND r.finalized_revision IS NULL)))
  AND NOT EXISTS (
      SELECT FROM ingest.recording_episode_processing p
      WHERE (p.organization_id,p.project_id,p.region_code,p.recording_id)=
            (old.organization_id,old.project_id,old.region_code,old.recording_id)
        AND (p.status NOT IN ('FAILED','DISCARDED') OR p.data_ref IS NOT NULL))
  AND NOT EXISTS (
      SELECT FROM annotation.annotation_tasks a
      WHERE (a.organization_id,a.project_id,a.region_code)=(old.organization_id,old.project_id,old.region_code)
        AND split_part(a.rollout_id,':',1)=old.recording_id)
  AND NOT EXISTS (
      SELECT FROM episode_store.versions v
      WHERE (v.organization_id,v.project_id,v.region_code)=(old.organization_id,old.project_id,old.region_code)
        AND split_part(v.input_document->'identity'->>'rollout_id',':',1)=old.recording_id)
"""


def batch_prefix(scope, ident):
    if not re.fullmatch(r"[0-9a-f]{64}", ident) or any(not part or "/" in part for part in scope):
        raise ValueError("invalid batch identity")
    return "/".join(("derived-mcap", *scope, ident)) + "/"


def receipt(storage, prefix):
    key = prefix + "receipt.json"
    head = storage.head(key)
    if head is None:
        return None
    if not 0 < head.size <= 16 * 1024**2:
        raise ValueError("invalid batch receipt size")
    doc = json.loads(b"".join(storage.read_chunks(key)))
    upload = RecordingUpload.model_validate(doc["upload"])
    ident = prefix.split("/")[-2]
    scope = upload.scope
    if (prefix != batch_prefix((scope.organization_id,scope.project_id,scope.region_code),ident)
            or upload.upload_id != uuid5(NAMESPACE_URL,"native-mcap:" + ident)):
        raise ValueError("batch receipt identity mismatch")
    return upload, [RecordingAsset.model_validate(item) for item in doc["assets"]]


def old_previews(parsed):
    if not parsed:
        return set()
    upload, assets = parsed
    keys = set()
    for asset in assets:
        if asset.manifest.role == "RAW_VIDEO":
            for selected in (True, False):
                key = preview_key(upload, asset, assets, selected=selected)
                keys.update((key, key + ".json"))
    return keys


class SupersededBatches:
    def __init__(self, connections, storage, repository, *, clock):
        self.connections = connections
        self.storage = storage
        self.repository = repository
        self.clock = clock
        self.stores = {"store": storage}
        self.local = None
        bucket = getattr(storage, "_bucket", None)
        if isinstance(bucket, CacheRoutingBucket):
            self.local = bucket.cache
            # Inspect the real remote directory as well: older releases put some
            # now-local asset classes in OSS. The routing adapter hides those copies.
            self.stores = {"oss": OssObjectStorage(bucket.remote), "local": OssObjectStorage(self.local)}

    def policy(self, scope, ready_at):
        current = select_policy(self.repository, scope[1], ACTION)
        if current is not None and ready_at <= self.clock() - timedelta(days=current.minimum_age_days):
            return current
        return None

    def objects(self, prefix, previews):
        plan = []
        for name, store in self.stores.items():
            keys = ({item.key for item in self.local.listing(prefix)} if name == "local"
                    else set(store.list_object_keys(prefix)))
            if any(not key.startswith(prefix) for key in keys):
                raise ValueError("batch listing escaped its scope")
            for key in sorted(keys | previews):
                head = store.head(key)
                if head is not None:
                    plan.append(dict(location=name,key=key,size=head.size,etag=head.etag))
        return plan

    def sweep(self, scope):
        ctx = current_request_context()
        if tuple(scope) != (ctx.organization_id,ctx.project_id,ctx.region_code):
            raise PermissionError("exact project scope required")
        if select_policy(self.repository,scope[1],ACTION) is None:
            return
        with self.connections() as db:
            candidates = db.execute(CANDIDATES + " ORDER BY old.created_at,old.processing_id",scope).fetchall()
        for row in candidates:
            if self.policy(scope,row[6]) is None:
                continue
            try:
                self.retire(scope, row[0], row[1])
            except Exception:
                logger.exception("superseded batch cleanup deferred",extra={"processing_id":row[1]})

    def retire(self, scope, raw_id, ident):
        with self.connections() as db:
            # Same lock order as the parser. The session lock spans the commit
            # publishing the plan and releases on disconnect after a worker crash.
            got = db.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))",("mcap:"+ident,)).fetchone()[0]
            if not got:
                return
            lock_source(db,scope,raw_id)
            db.execute("""SELECT r.recording_id FROM ingest.continuous_recordings r
                JOIN ingest.mcap_processing_files m USING(organization_id,project_id,region_code,recording_id)
                WHERE (m.organization_id,m.project_id,m.region_code,m.processing_id)=(%s,%s,%s,%s)
                FOR UPDATE OF r""",(*scope,ident)).fetchall()
            row = db.execute(CANDIDATES+" AND old.processing_id=%s AND old.raw_source_id=%s",(*scope,ident,raw_id)).fetchone()
            if not row or self.policy(scope,row[6]) is None:
                return
            _, _, recording, latest, latest_recording, original, ready_at = row
            prefix = batch_prefix(scope,ident)
            saved = db.execute("""SELECT plan FROM ingest.mcap_batch_retirements WHERE
                (organization_id,project_id,region_code,processing_id)=(%s,%s,%s,%s)""",(*scope,ident)).fetchone()
            if saved:
                plan = saved[0]
            else:
                uid = uuid5(NAMESPACE_URL,"native-mcap:"+ident)
                if db.execute("""SELECT 1 FROM ingest.recording_upload_assets
                      WHERE starts_with(object_key,%s) AND upload_id<>%s LIMIT 1""",(prefix,uid)).fetchone():
                    return
                if db.execute("""SELECT 1 FROM ingest.mcap_processing_files
                      WHERE starts_with(source_document->>'object_key',%s)
                      OR starts_with(source_document->'takeover_file'->>'object_key',%s) LIMIT 1""",(prefix,prefix)).fetchone():
                    return
                replacement = receipt(self.storage,batch_prefix(scope,latest))
                if not replacement or replacement[0].command.recording_id != latest_recording:
                    raise ValueError("replacement receipt unavailable")
                plan = self.objects(prefix,old_previews(receipt(self.storage,prefix)))
                if plan:
                    # Frozen source (including an external ZIP member) must still
                    # be readable. Only the isolated superseded output is disposable.
                    verify_object(self.storage,original["object_key"],original["size"],original["sha256"])
                if self.policy(scope,ready_at) is None:
                    return
                db.execute("""INSERT INTO ingest.mcap_batch_retirements
                      (organization_id,project_id,region_code,processing_id,replacement_id,replacement_ready_at,plan)
                      VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)""",(*scope,ident,latest,ready_at,json.dumps(plan)))
                if recording:
                    db.execute("""UPDATE ingest.continuous_recordings SET superseded_by_processing_id=%s
                        WHERE (organization_id,project_id,region_code,recording_id)=(%s,%s,%s,%s)""",
                        (latest,*scope,recording))
                db.commit()
            # The exact manifest remains committed until every object is absent.
            for item in plan:
                current = self.policy(scope,ready_at)
                if current is None:
                    return
                store = self.stores[item["location"]]
                head = store.head(item["key"])
                if head is not None:
                    if (head.size,head.etag)!=(item["size"],item["etag"]):
                        raise ValueError("retirement target changed")
                    store.delete_object(item["key"])
                    if store.head(item["key"]) is not None:
                        raise RuntimeError("retirement target still exists")
            current = self.policy(scope,ready_at)
            if current is None:
                return
            event = dict(cache_action=ACTION.value,processing_id=ident,replacement_id=latest,
                         object_count=len(plan),reclaimed_bytes=sum(i["size"] for i in plan),
                         policy_version=current.version,minimum_age_days=current.minimum_age_days)
            db.execute("""UPDATE ingest.mcap_batch_retirements SET completed_at=now()
                WHERE (organization_id,project_id,region_code,processing_id)=(%s,%s,%s,%s)""",(*scope,ident))
            db.execute("""INSERT INTO storage.lifecycle_audit_events
                (project_id,audit_id,policy_id,actor_id,action,request_id,details,occurred_at)
                VALUES (%s,%s,%s,'storage-inventory-worker','storage.superseded_batch.expired',%s,%s::jsonb,now())""",
                (scope[1],str(uuid5(NAMESPACE_URL,"batch-retire:"+"/".join((*scope,ident)))),
                 current.policy_id,"retire-"+ident,json.dumps(event)))
