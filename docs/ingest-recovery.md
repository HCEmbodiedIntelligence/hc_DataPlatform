# Parallel ingest and recovery

MCAP and native LeRobot use the same `IngestRolloutWorkflow`. New inputs from
both adapters enable `parallel_preparation`; old persisted inputs default to
false and retain their version-bound execution for history replay.

## Preparation and publication

Raw verification, QC, alignment and video encoding can overlap between files or
episodes in the same Dataset. Encoded videos are uploaded to private,
SHA-256-verified `staging/prepared-media/` objects. No public media artifact or
Dataset version is reserved during encoding. LeRobot original-video references
use the same preparation receipt without copying or re-encoding the original.

Only final publication is serialized per organization/project/Dataset. A
PostgreSQL advisory transaction lock covers allocation of the final version,
publication of already-encoded media under that version, the Lance commit and
the product projection. Busy commits wait on durable Temporal timers, releasing
activity slots. They reuse their prepared videos when the Dataset advances.

The catalog additionally validates `expected_version` inside its own writer
lock, after receipt reconciliation and before appending. This protects against
other writers using the older commit API. Idempotent recovery uses the original
rollout receipt, even when newer versions exist. An incomplete product projection
blocks later commits until its owner repairs it.

## Bounded scheduling

`HC_INGEST_MAX_ACTIVE_PER_DATASET` defaults to 8. A shared PostgreSQL dispatch
guard reserves a PENDING job before launch and caps active preparation across
all workers. The workflow also reserves at entry, so native LeRobot children and
robot-upload processing use the same limit. Waiting uploads remain in the
durable outbox; native children waiting for admission release activity slots.
Outbox capacity waits recheck after five seconds instead of accumulating the
exponential backoff used for transport failures.
Active legacy workflows exclude new parallel preparation until they finish.

Both LeRobot parent workflows process at most `max_concurrent_episodes` (default
4) simultaneously. Each drains its in-flight episodes before continuing as new
after 20 episodes. Failures and retries remain per episode. The reconstructible
LeRobot cache uses separate pinned entries per source/episode, so concurrent
episodes cannot delete each other's local chunks. Cache capacity and eviction
still apply; immutable Raw objects are never evicted.

The original local 8088 deployment runs four worker processes, each with one main
activity slot and one media slot. This allows Python parsing to use multiple CPU
cores. Media encoding has a shared four-slot limit and two FFmpeg threads per
encoder. Publishing prepared media does not consume an encoder slot. Other
deployments can choose worker replicas and these limits independently.

## Recording boundaries and interior gaps

New MCAP format profiles (version 3) and native LeRobot profiles use
`be06-qc/3`. Existing v1/v2 profiles and immutable reports retain their original
semantics. Each required topic's initial/final waiting period is recorded as
`QC_LEADING_IDLE` / `QC_TRAILING_IDLE` with `info` severity. These annotations do
not make a recording RISK. The final sample covers one nominal sample period;
static optional topics do not narrow the common active window.

Every interior gap exceeding the profile's missing-sample threshold produces a
separate warning, even when a larger boundary gap exists. Missing entire topics,
corrupt images, and an empty common active window still require review. Normal
boundary annotations are excluded from the problem-data warning count/range.

Ingest aligns all modalities on the intersection of their active windows,
renumbering derived steps and video frames from zero. Raw files and their original
timestamps remain intact. The LeRobot exporter also trims invalid prefix/suffix
frames from existing frozen datasets, uses the identical retained step selection
for Parquet and every video, and writes `boundary_trim` provenance into
`meta/annotations.json`. Interior invalid frames are retained and identified;
human review/cleaning decides their treatment. Exports use a new immutable v5
artifact identity so older archives cannot be mistaken for cropped output.

Historical reports need a fresh timestamp scan: the old single-largest-gap
summary cannot prove that a smaller interior gap is absent. Preserve the original
reports, publish a new profile/report version, and explicitly reserve any released
quality outcomes with `retry_quality=True` before restarting ingest. Ordinary
transport recovery never retries a quality outcome automatically.

## Cancellation and recovery

Terminal job state is persisted after cleanup. Cancellation of a synchronous
activity waits for its thread and encoder to exit before acknowledging; final
commit also waits for cancellation completion. Prepared objects are deleted by
normal cleanup, with a TTL sweeper for interrupted preparations. Original-video
references are never included in derivative cleanup.

Selected transient workflow failures have at most three attempts, separated by
durable 30- and 60-second timers. Quality risk/rejection and explicit user
cancellation are not retried into success. Waiting recovery is displayed as
“等待重试”; explicit cancellations appear as “已取消”.

Closed historical workflows need an explicit recovery operation. Start a fresh
execution with the validated original input, `parallel_preparation=true`, and
the same dispatch guard. Do not reset histories whose first workflow task already
contained cancellation. Preserve raw objects, successful versions and audit
history. During deployment, drain/restart all workers before enabling additional
replicas; the legacy-input guard protects work resumed from old histories.

## Validation

Tests cover concurrent preparation, actual FFmpeg output, immutable media binding
to different final versions, recovery after a catalog commit without duplicate
encoding, LeRobot original-video references, admission limits, legacy exclusion,
bounded episode fan-out, independent cache pins, cancellation, and history replay.
The real LeRobot upload-to-QC/media/Lance/review/export suite also runs against
isolated PostgreSQL databases and a test object-store bucket.
