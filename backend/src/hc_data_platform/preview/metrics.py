"""Low-cardinality media-pipeline metrics.

Artifact keys, sessions, users, projects, and object keys are deliberately absent
from labels. ``profile_id`` is safe because it comes from a finite server allowlist.
"""

from prometheus_client import Counter, Gauge, Histogram

PREVIEW_CACHE_HIT = Counter("preview_cache_hit_total", "READY preview authorization hits.")
PREVIEW_CACHE_MISS = Counter(
    "preview_cache_miss_total", "Authorizations requiring a durable media job."
)
PREVIEW_JOBS_QUEUED = Gauge(
    "preview_jobs_queued", "Preview generation jobs observed in QUEUED state."
)
PREVIEW_JOBS_RUNNING = Gauge(
    "preview_jobs_running", "Preview jobs running in this media worker."
)
PREVIEW_JOBS_FAILED = Gauge("preview_jobs_failed", "Failed preview generation attempts.")
PREVIEW_GENERATION_SECONDS = Histogram(
    "preview_generation_seconds",
    "Media generation and immutable publication latency.",
    ("profile_id",),
)
PREVIEW_FRAMES = Counter(
    "preview_frames_total", "Frames consumed by preview generators.", ("profile_id",)
)
PREVIEW_SOURCE_BYTES_READ = Gauge(
    "preview_source_bytes_read", "JPEG source bytes streamed into FFmpeg."
)
PREVIEW_OUTPUT_BYTES = Gauge(
    "preview_output_bytes", "Verified immutable preview bytes published."
)
PREVIEW_PEAK_IN_FLIGHT_FRAMES = Gauge(
    "preview_peak_in_flight_frames", "Peak source frames retained by the encoder."
)
PREVIEW_OBJECT_GET = Counter(
    "preview_object_get_total", "Object-store GETs performed by the preview pipeline."
)
FFMPEG_PROCESS = Counter(
    "ffmpeg_process_total", "FFmpeg processes started by the media worker."
)
PREVIEW_GC_DELETED_BYTES = Gauge(
    "preview_gc_deleted_bytes", "Exact-manifest preview bytes deleted by GC."
)
PREVIEW_GC_FAILURES = Counter(
    "preview_gc_failures_total", "Preview artifact deletions requiring retry."
)
PREVIEW_STORAGE_BYTES = Gauge(
    "preview_storage_bytes", "READY preview bytes visible to the collector scope."
)
PREVIEW_STORAGE_ARTIFACTS = Gauge(
    "preview_storage_artifacts", "READY artifacts visible to the collector scope."
)
PROJECTION_SCAN = Counter("projection_scan_total", "Raw ingest projection scans.")
PROJECTION_SOURCE_PASSES = Gauge(
    "projection_source_passes", "Sequential source passes made by ingest projection."
)
AUTO_ANNOTATION_SAMPLE_RATIO = Gauge(
    "auto_annotation_sample_ratio",
    "Selected auto-annotation frames divided by source camera frames.",
)
