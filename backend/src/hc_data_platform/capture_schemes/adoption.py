"""Evidence-backed adoption of a legacy dataset without rewriting its packages.

The old physical topic schema and collection identities stay frozen. A separate
immutable receipt proves every committed source matches the shared definition.
New uploads must still pass that definition before reusing the physical schema.
"""

import hashlib
import json

from hc_data_platform.episode_store.models import CommitReceipt, PackageManifest
from hc_data_platform.episode_store.models import digest as package_digest
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot

from . import plans, schemes
from .common import audit, digest, encoded, lock, now, reject
from .models import Sample
from .schema import native_topic, structure_contract


def adoption_key(scope, dataset):
    return encoded([scope[1], scope[2], dataset])


def physical_schema(db, scope, proposed):
    """Return an old physical schema only with an exact immutable adoption receipt."""
    row = db.execute(
        """SELECT document,content_hash FROM episode_store.dataset_schemas
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
        (*scope, proposed.dataset_id),
    ).fetchone()
    if not row:
        return proposed
    if row[0].get("semantic_contract") is not None:
        old = DatasetSchemaSnapshot.model_validate(row[0])
        if (
            package_digest(row[0]) == row[1]
            and old.fields == proposed.fields
            and structure_contract(old.semantic_contract)
            == structure_contract(proposed.semantic_contract)
        ):
            return old
        return proposed
    proof = db.execute(
        """SELECT document FROM ingest.capture_legacy_references
        WHERE organization_id=%s AND legacy_kind='DATASET_CONTRACT'
        AND legacy_key=%s AND source_sha256=%s""",
        (scope[0], adoption_key(scope, proposed.dataset_id), row[1]),
    ).fetchone()
    if not proof:
        return proposed
    old = DatasetSchemaSnapshot.model_validate(row[0])
    if (
        package_digest(row[0]) != row[1]
        or proof[0]["legacy_schema_sha256"] != row[1]
        or old.fields != proposed.fields
    ):
        reject("CAPTURE_DATASET_INCOMPATIBLE", "上传数据与已核验的历史数据集结构不同")
    frozen = db.execute(
        """SELECT contract_sha256,document FROM episode_store.dataset_capture_contracts
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
        (*scope, proposed.dataset_id),
    ).fetchone()
    if (
        not frozen
        or frozen[0] != digest(frozen[1])
        or proof[0]["output_contract_sha256"] != frozen[0]
        or structure_contract(frozen[1]) != structure_contract(proposed.semantic_contract)
    ):
        reject("CAPTURE_DATASET_INCOMPATIBLE", "历史数据集缺少一致的采集方案约束")
    return old


def adopt_dataset(db, scope, task, reference, samples, read_object, actor):
    """Operator transaction: inspect original bytes; no client-supplied pass flag."""
    record = plans.task_record(db, scope, task, for_update=True)
    dataset = record["dataset_id"]
    lock(db, "dataset", *scope, dataset)
    document = schemes.version(db, scope[0], reference, new_use=True)
    output = document["output"]
    row = db.execute(
        """SELECT document,content_hash FROM episode_store.dataset_schemas
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
        (*scope, dataset),
    ).fetchone()
    if not row or row[0].get("semantic_contract") is not None or package_digest(row[0]) != row[1]:
        reject("CAPTURE_DATASET_REVIEW_REQUIRED", "需要可核验的历史物理数据结构")
    expected = {native_topic(name): "json" for name in output["features"]}
    expected["/metadata/source"] = "json"
    if row[0]["fields"] != expected:
        reject("CAPTURE_DATASET_INCOMPATIBLE", "历史话题与方案不一致，不能直接绑定")
    key = adoption_key(scope, dataset)
    prior = db.execute(
        """SELECT document FROM ingest.capture_legacy_references WHERE organization_id=%s
        AND legacy_kind='DATASET_CONTRACT' AND legacy_key=%s AND source_sha256=%s""",
        (scope[0], key, row[1]),
    ).fetchone()
    if prior:
        if prior[0]["output_contract_sha256"] != digest(output):
            reject("CAPTURE_DATASET_INCOMPATIBLE", "历史数据集已经绑定其他语义")
        return prior[0]
    versions = db.execute(
        """SELECT status,receipt_document FROM episode_store.versions
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s
        ORDER BY data_version_id FOR SHARE""",
        (*scope, dataset),
    ).fetchall()
    if not versions or any(status != "COMMITTED" for status, _ in versions):
        reject("CAPTURE_DATASET_REVIEW_REQUIRED", "历史数据仍在处理或缺少完整成果")
    evidence = []
    for _, receipt_document in versions:
        receipt = CommitReceipt.model_validate(receipt_document)
        prefix = receipt.data.package_prefix
        raw_manifest = read_object(prefix + "/hc/manifest.json")
        if hashlib.sha256(raw_manifest).hexdigest() != receipt.manifest_sha256:
            reject("CAPTURE_SOURCE_INVALID", "历史成果清单校验失败")
        manifest = PackageManifest.model_validate_json(raw_manifest)
        if (
            manifest.input.identity != receipt.data.identity
            or manifest.content_hash != receipt.data.content_hash
        ):
            reject("CAPTURE_SOURCE_INVALID", "历史成果身份与清单不一致")
        source_entry = next(f for f in manifest.files if f.path == "hc/source.json")
        raw_source = read_object(prefix + "/hc/source.json")
        if (
            len(raw_source) != source_entry.size
            or hashlib.sha256(raw_source).hexdigest() != source_entry.sha256
        ):
            reject("CAPTURE_SOURCE_INVALID", "历史成果来源校验失败")
        source = json.loads(raw_source)
        if (
            source.get("format") != "lerobot_v3"
            or source.get("source_sha256") != manifest.input.source_sha256
        ):
            reject("CAPTURE_DATASET_REVIEW_REQUIRED", "历史成果缺少可追溯的 LeRobot 来源")
        raw = db.execute(
            """SELECT dataset_id,manifest_key FROM ingest.raw_sources
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
            (*scope, source["raw_upload_id"]),
        ).fetchone()
        if (
            not raw
            or raw[0] != dataset
            or hashlib.sha256(read_object(raw[1])).hexdigest() != source["source_sha256"]
        ):
            reject("CAPTURE_SOURCE_INVALID", "历史原始数据不属于当前数据集或摘要不匹配")
        sample = Sample(
            project_id=scope[1],
            region_code=scope[2],
            raw_source_id=source["raw_upload_id"],
            source_format="LEROBOT_V3",
            episode_index=source["episode_index"],
        )
        checked = samples.validate(scope[0], document, sample)
        if checked["status"] != "PASSED":
            reject("CAPTURE_DATASET_INCOMPATIBLE", "历史原始数据与方案不兼容", evidence=checked)
        evidence.append(
            {
                "receipt": receipt_document,
                "source_sha256": source_entry.sha256,
                "validation": checked,
            }
        )
    proof = {
        "schema_version": "capture-dataset-adoption/v1",
        "scope": list(scope),
        "dataset_id": dataset,
        "collection_task_id": task,
        "scheme": reference.model_dump(mode="json"),
        "legacy_schema_sha256": row[1],
        "output_contract_sha256": digest(output),
        "evidence": evidence,
        "adopted_at": now(),
        "adopted_by": actor,
    }
    db.execute(
        """INSERT INTO episode_store.dataset_capture_contracts
        (organization_id,project_id,region_code,dataset_id,contract_sha256,document)
        VALUES(%s,%s,%s,%s,%s,%s::jsonb)""",
        (*scope, dataset, digest(output), encoded(output)),
    )
    db.execute(
        """INSERT INTO ingest.capture_legacy_references
        (organization_id,legacy_kind,legacy_key,source_sha256,scheme_id,scheme_version,document)
        VALUES(%s,'DATASET_CONTRACT',%s,%s,%s,%s,%s::jsonb)""",
        (scope[0], key, row[1], reference.scheme_id, reference.version, encoded(proof)),
    )
    audit(db, scope[0], reference.scheme_id, actor, "ADOPT_DATASET_CONTRACT", proof)
    return proof
