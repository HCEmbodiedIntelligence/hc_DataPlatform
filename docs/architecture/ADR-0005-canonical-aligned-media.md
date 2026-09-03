# ADR-0005: canonical aligned media is an ingest artifact

Status: accepted on 2026-08-31.

## Decision

Camera playback media is created exactly once during ingest. The only production flow is:

1. retain immutable raw MCAP and manifest objects;
2. verify MCAP integrity and declared topics, then run QC;
3. causally align every modality to fixed 30 Hz and publish a bounded, short-lived Arrow staging
   artifact;
4. submit cameras in batches of two to the internal media queue; the global PostgreSQL capacity
   lease limits FFmpeg work across all media pods;
5. stream one JPEG at a time to FFmpeg stdin with backpressure and publish one immutable H.264
   CRF20/yuv420p MP4 plus its exact publication-intent receipt per camera;
6. after every manifest camera has a READY receipt with the same step/frame/PTS timeline, commit
   Lance rows containing media artifact/frame references, state, action, validity, and alignment
   lineage—never JPEG/base64 camera payloads;
7. mark all media receipts Dataset-committed and only then create the annotation task.

P04, P06, and P08 call `POST /api/v1/aligned-media/authorize` and play the returned MP4 with the
native browser video element and HTTP Range requests. Authorization performs one scoped receipt
lookup, signs or resolves the existing object, and audits the grant. It has no FFmpeg, job-create,
queue, or Lance dependency. Clients renew a grant before expiry and may retry once after a media
request error.

## Failure and recovery

- Generation identity includes tenant, Dataset/version, rollout, camera, source digest, alignment
  digest/version, profile, and pipeline revision. A retry either returns the verified READY
  receipt or resumes/re-encodes the same immutable identity.
- Worker jobs and global media slots use owner, attempt-token, lease-expiry, heartbeat, and fencing
  checks. A stale worker cannot publish a winning state transition.
- Publication writes `media.mp4` and `publication-intent.json` with create-only semantics and
  verifies size/SHA metadata. Rollback and bundle-abandonment delete only receipt-listed keys.
- A six-hour Dataset-commit lease and 24-hour READY protection prevent the orphan reconciler from
  deleting media while Lance commit is in flight. Dataset-committed media is not TTL/LRU-evicted.
- If any camera fails before the Dataset commit, successful siblings enter `ABANDONING`, their
  exact receipt members are deleted, and they become retryable `FAILED`. Lance and annotation
  visibility remain closed.
- Alignment/projection staging is TTL-bounded and deleted by exact key. Raw MCAP is never removed
  by this workflow. Restore reconciliation rebuilds derived state from raw and committed aligned
  artifacts; it does not invoke a browser-facing generation API.

## Forward migration and retained history

`backend/migrations/preview/0001`–`0003` are immutable historical migrations and remain checksum
bound. Forward migration `0004_canonical_aligned_media.sql` creates the independent
`aligned_media` schema; runtime code never reads or writes the old preview tables.

Two read-only compatibility adapters remain for already persisted documents:

- `RuntimeConfigValues` maps historical `scheduling.preview_gc_interval_seconds` snapshots to
  `scheduling.media_maintenance_interval_seconds`; new writes cannot use the old key.
- `DatasetPageEpisodeStream` discards historical `preview_binding` projection fields; it never
  turns them into media access or work.

Remove those adapters only after the oldest retained runtime-config and Dataset projection
snapshots have crossed the supported restore/upgrade window. The historical migrations remain
forever; deleting them would invalidate migration checksums and disaster-recovery rehearsals.

## Capacity evidence

`backend/benchmarks/aligned_media_pipeline.py` and
`backend/tests/load/results/aligned-media-capacity-20260831.json` are local component evidence for
four cameras, 30 Hz, 60 seconds, upload concurrency 1/2/4, global media capacity two, and 100
authorize-plus-Range clients. The result is deliberately labelled
`LOCAL_COMPONENT_CAPACITY_NOT_PRODUCTION`; deployed PostgreSQL/S3/network/pod limits and headroom
still require environment-specific measurement.
