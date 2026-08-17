# BE-12 pilot capacity report

Status: **NOT PASSED — the complete pipeline could not run and the MinIO upload stage was below
the required throughput.**

## Target

- Daily volume: 5,000,000,000,000 bytes (5 decimal TB).
- Required headroom: 30%.
- Sustained threshold: 75,231,481.48 bytes/s over the full ingest-to-export path.
- Control-plane cases: one 20 GiB rollout manifest and 50 concurrent upload sessions.

## Measured host

- Environment: WSL2 Linux `6.18.33.2-microsoft-standard-WSL2`, x86_64.
- CPU: Intel Core i7-14650HX, 24 logical CPUs exposed to the VM.
- Memory: 15 GiB visible to the VM, 4 GiB swap.
- Storage mount: `/dev/sdd`, 1 TB virtual disk, approximately 940 GiB free at measurement time.
- Limitation: this is a single developer/pilot-candidate VM, not the declared production object
  store, Temporal, PostgreSQL, Lance, transcode, and export cluster.

## Commands and data

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/load/test_capacity_controls.py
python tests/load/inprocess_http_probe.py --concurrency 50 \
  --rollout-size 21474836480
python tests/load/capacity_probe.py --size-bytes 536870912 --samples 7 \
  --output tests/load/results/local-capacity.json
BE12_MINIO_ACCESS_KEY='<test key>' BE12_MINIO_SECRET_KEY='<test secret>' \
  python tests/load/minio_throughput_probe.py \
  --endpoint http://127.0.0.1:59300 --bucket be12-capacity \
  --access-key-env BE12_MINIO_ACCESS_KEY --secret-key-env BE12_MINIO_SECRET_KEY \
  --size-bytes 268435456 --samples 7
```

The synthetic data is a deterministic 512 MiB local file read seven times through SHA-256. The
JSON result is authoritative for local P50/P95/P99 latency and throughput. It is not an E2E
rollout sample and therefore cannot prove the daily target.

| Measured stage | P50 | P95 | P99 | Result against 75.23 MB/s |
| --- | ---: | ---: | ---: | --- |
| Durable local write latency | 0.379577 s | 0.379577 s | 0.379577 s | Diagnostic only |
| Sequential local read + SHA latency | 0.356526 s | 0.383495 s | 0.383495 s | Diagnostic only |
| Sequential local read + SHA throughput | 1,505.84 MB/s | 1,576.20 MB/s | 1,576.20 MB/s | Stage-only pass |
| In-process FastAPI session latency (50 concurrent, signed JWT) | 65.15 ms | 89.08 ms | 95.40 ms | Control-plane pass only |
| Historical deployed kind API response latency (50 concurrent) | 76.54 ms | 96.84 ms | 98.80 ms | FAIL; all responses were 401 |
| MinIO multipart upload latency (7×256 MiB) | 4.752740 s | 4.795194 s | 4.795194 s | FAIL stage target |
| MinIO multipart upload throughput | 56.48 MB/s | 57.64 MB/s | 57.64 MB/s | FAIL vs 75.23 MB/s |
| MinIO download + SHA latency | 0.982642 s | 1.100629 s | 1.100629 s | Stage diagnostic |
| MinIO download + SHA throughput | 273.18 MB/s | 396.70 MB/s | 396.70 MB/s | Stage-only pass |

The minimum observed local SHA throughput was 1,399.94 MB/s. Reads after the first sample may be
served from the Linux page cache; the result identifies SHA as non-bottlenecking on this VM but
does not represent object-store or network throughput.

The current in-process HTTP run completed in 0.136490 seconds (366.33 session creations/s): all 50
requests returned 201, produced 50 unique sessions, and stored zero object bodies. It exercises
Pydantic validation, the production `create_app` request/JWT middleware with a signed test token,
the actual FastAPI router, and an explicitly selected in-memory runtime backend. It excludes
sockets, proxy, TLS, object storage, and the data plane.

The retained 2026-08-14 Kubernetes Service run used `upload_control_plane.py` with 50 concurrent
20 GiB manifests. Every request returned 401 under the old application source; no upload session
was created. Current source fixes that auth path in-process, but the old response latencies still
characterize only fast authentication rejection and must not be used as successful pilot
control-plane capacity.

The MinIO result is retained in `tests/load/results/minio-capacity.json`. Seven unique 256 MiB
objects were uploaded using 16 MiB multipart chunks with four client threads, then streamed back
and SHA-256 verified. Minimum upload throughput was 55.98 MB/s and P50 was 56.48 MB/s, about 25%
below the required 75.23 MB/s before adding API, network, Temporal, Lance, transcode, or export
cost. The same-host Docker path is favorable rather than production-equivalent, so this is a
demonstrated bottleneck, not a reason to extrapolate a pass.

## Bottlenecks and unmet items

- A superseded Worker target installed the validation-only LeRobot/PyTorch/CUDA stack. Its locked
  dependency layer took 856.6 seconds on this host and pulled multiple 50–500 MiB wheels before
  final image export was cancelled. BE-01 removed that stack from the production target and
  closed BE12-010; those timings are historical diagnostics, not current pipeline throughput.
- The historical pilot API rejected all 50 external requests with 401. BE12-003 is closed in
  source, but successful external session capacity is still unmeasured until the pilot is rerun.
- No sustained production Worker, PostgreSQL catalog, physical Lance commit, FFmpeg transcode,
  or LeRobot reload throughput was measured.
- Temporal Worker process recovery and MinIO network recovery pass as bounded fault tests, but
  they are not a sustained pipeline throughput measurement.
- End-to-end P50/P95/P99, saturation, retry rate, and the 75.23 MB/s headroom gate remain unmet.

No 5 TB/day pass claim is made; the only measured object-store upload stage is already below the
required sustained rate.
