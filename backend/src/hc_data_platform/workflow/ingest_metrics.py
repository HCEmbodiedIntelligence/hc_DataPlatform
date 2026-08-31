"""Low-cardinality metrics for the single-pass ingest projection."""

from prometheus_client import Counter, Gauge

PROJECTION_SCAN = Counter("projection_scan_total", "Raw ingest projection scans.")
PROJECTION_SOURCE_PASSES = Gauge(
    "projection_source_passes",
    "Sequential source passes made by ingest projection.",
)
AUTO_ANNOTATION_SAMPLE_RATIO = Gauge(
    "auto_annotation_sample_ratio",
    "Selected auto-annotation frames divided by source camera frames.",
)
