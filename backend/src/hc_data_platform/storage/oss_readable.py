"""Readable physical directories. Logical package keys and exported paths stay stable."""

import hashlib
import re
from pathlib import PurePosixPath
from urllib.parse import unquote

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.dbapi import psycopg_connection_factory

MARKER = "_layout/readable-directories-v1"
HEX = re.compile(r"[0-9a-f]{64}")
CATEGORIES = {"raw", "derived-mcap", "episodes", "collections", "annotation-files"}


def short(value):
    return hashlib.sha256(value.encode()).hexdigest()[:10]


def label(value):
    value = re.sub(r"[^\w.\-]+", "-", str(value), flags=re.UNICODE).strip("-._")
    return value[:70].rstrip("-._") or "未命名"


def group_key(key):
    p = key.split("/")
    kind = p[0]
    if kind == "raw":
        if len(p) >= 4 and re.fullmatch(r"[a-f0-9]{32}", p[3]):
            return "/".join(p[:4])
        if len(p) >= 6 and p[5].startswith("riu-"):
            return "/".join(p[:6])
    if kind == "derived-mcap" and len(p) >= 5 and HEX.fullmatch(p[4]):
        return "/".join(p[:5])
    if kind in ("episodes", "collections"):
        marker = "id-dv_" if kind == "episodes" else "id-cv_"
        for i, part in enumerate(p):
            if part.startswith(marker) and HEX.fullmatch(part[len(marker):]):
                return "/".join(p[:i+1])
    if kind == "annotation-files" and len(p) >= 5 and p[4].startswith("id-"):
        return "/".join(p[:5])
    return None


def readable_prefixes(key):
    group = group_key(key)
    if not group:
        return ()
    tail = key[len(group):].strip("/").split("/")
    if group.startswith("derived-mcap/") and HEX.fullmatch(tail[0]):
        return (group, group + "/" + tail[0])
    return (group,)


def recording_label(filename, recording_id, date=None):
    if filename:
        return "录制-" + label(PurePosixPath(filename).stem)
    if date:
        return "录制-" + label(date)
    return "录制-" + short(recording_id) if recording_id else "录制信息未登记"


def recording_details(db, rollout):
    recording, _, segment = (rollout or "").partition(":")
    row = db.execute("""
        SELECT (SELECT source_path FROM ingest.mcap_processing_files m
                WHERE m.organization_id=r.organization_id AND m.project_id=r.project_id
                  AND m.region_code=r.region_code AND m.recording_id=r.recording_id
                ORDER BY m.created_at LIMIT 1),
               to_char(r.capture_started_at AT TIME ZONE 'Asia/Shanghai','YYYYMMDD_HH24MISS'),
               (SELECT s.ordinal FROM ingest.recording_episode_slices s
                WHERE s.organization_id=r.organization_id AND s.project_id=r.project_id
                  AND s.region_code=r.region_code AND s.recording_id=r.recording_id
                  AND s.episode_id=%s ORDER BY s.revision DESC LIMIT 1)
        FROM ingest.continuous_recordings r WHERE r.recording_id=%s LIMIT 1
        """, (segment, recording)).fetchone()
    filename, stamp, ordinal = row or (None, None, None)
    segment_name = ("整段" if segment == "episode_full_recording" else
                    f"片段-{ordinal + 1}" if ordinal is not None else "片段")
    return recording_label(filename, recording, stamp), segment_name


def describe_group(db, group, hint=None):
    """Use database facts when present, otherwise a qualified, collision-safe identifier."""
    p = [unquote(v.removeprefix("id-")) for v in group.split("/")]
    kind = p[0]
    hint = hint or {}
    task = dataset = None
    folders = []
    tag = short(group)
    if kind == "episodes":
        task = p[-3]
        row = db.execute("""
            SELECT dataset_id,input_document->'identity'->>'rollout_id',version
            FROM episode_store.versions WHERE data_version_id=%s AND episode_id=%s LIMIT 1
            """, (p[-1],p[-2])).fetchone()
        if row:
            dataset,rollout,version = row
            recording,segment = recording_details(db,rollout)
            folders = [recording, f"{segment}-版本{version}-{tag}"]
        else:
            folders = [recording_label(None,None,p[4]), "片段版本-"+tag]
    elif kind == "collections":
        dataset = p[-2]
        row = db.execute("SELECT version FROM episode_store.collections WHERE dataset_id=%s AND collection_version_id=%s LIMIT 1",
                         (dataset,p[-1])).fetchone()
        version = row[0] if row else hint.get("version")
        folders = [f"数据集版本-{version}-{tag}" if version is not None else "数据集版本-"+tag]
    elif kind == "derived-mcap":
        row = db.execute("SELECT collection_task_id,source_path,recording_id FROM ingest.mcap_processing_files WHERE processing_id=%s LIMIT 1",
                         (p[-1],)).fetchone()
        task,filename,rec = row or (hint.get("collection_task_id"),hint.get("filename"),hint.get("recording_id"))
        folders = [recording_label(filename,rec,hint.get("capture_started_at")), "解析批次-"+tag]
    elif kind == "annotation-files":
        row = db.execute("SELECT dataset_id,rollout_id FROM annotation.annotation_tasks WHERE task_id::text=%s LIMIT 1",
                         (p[-1],)).fetchone()
        dataset,rollout = row or (hint.get("dataset_id"),hint.get("rollout_id"))
        recording,segment = recording_details(db,rollout)
        folders = [recording,segment+"-标注-"+tag]
    else:
        if len(p) == 6:
            task=p[3]
            folders = ["机器人直传-"+tag]
        else:
            dataset=p[2]
            row=db.execute("SELECT to_char(created_at AT TIME ZONE 'Asia/Shanghai','YYYYMMDD_HH24MISS') FROM ingest.raw_sources WHERE raw_source_id=%s LIMIT 1",(p[3],)).fetchone()
            folders=["上传批次-"+(row[0]+"-" if row else "")+tag]
    t=db.execute("""SELECT task_code,name FROM collection_tasks.collection_tasks
                 WHERE collection_task_id::text=%s OR dataset_id=%s ORDER BY task_code LIMIT 1""",
                 (task,dataset)).fetchone() if task or dataset else None
    task_name=("任务-"+str(t[0]).lstrip("0")+"-"+label(t[1])) if t else "任务未登记-"+short(task or dataset or group)
    return "/".join([kind,task_name,*folders])


def scoped_group(dsn,group):
    if current_request_context().project_id or current_request_context().organization_id:
        with psycopg_connection_factory(dsn)() as db:
            return describe_group(db,group)
    # Background health operations have no tenant and cannot resolve business names.
    raise RuntimeError("A project scope is required to name a new OSS business directory")


def asset_subfolder(group_path,subgroup,key):
    tail = key[len(subgroup):].strip("/")
    name = PurePosixPath(tail).stem if tail else ""
    names = {"flags":"深度有效标记","frames":"帧记录","profile":"深度参数",
             "report":"处理报告","normalization":"归一化配置","recording-config":"录制配置",
             "recording-media":"媒体索引","mcap-provenance":"采集来源"}
    purpose = names.get(name,"解析产物")
    return f"{group_path}/{purpose}-{short(subgroup)}"
