from prometheus_client import generate_latest

from hc_data_platform.preview import metrics


def test_required_preview_metric_names_are_exported_without_high_cardinality_labels() -> None:
    assert metrics.PREVIEW_CACHE_HIT is not None
    payload = generate_latest().decode("utf-8")
    required = {
        "preview_cache_hit_total",
        "preview_cache_miss_total",
        "preview_jobs_queued",
        "preview_jobs_running",
        "preview_jobs_failed",
        "preview_generation_seconds",
        "preview_frames_total",
        "preview_source_bytes_read",
        "preview_output_bytes",
        "preview_peak_in_flight_frames",
        "preview_object_get_total",
        "ffmpeg_process_total",
        "preview_gc_deleted_bytes",
        "preview_storage_bytes",
        "projection_scan_total",
        "projection_source_passes",
        "auto_annotation_sample_ratio",
    }

    for metric_name in required:
        assert f"# TYPE {metric_name} " in payload
    assert "session_id=" not in payload
    assert "user_id=" not in payload
    assert "object_key=" not in payload
