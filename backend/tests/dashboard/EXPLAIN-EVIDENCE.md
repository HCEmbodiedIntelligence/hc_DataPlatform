# P01 dashboard query-plan evidence

Measured on 2026-08-18 with PostgreSQL 16.10 in a task-exclusive container. The data is a
production-volume proxy, not a production export: 1,000,000 unlogged fact rows, 20 projects,
4 regions, and 200,000 collection jobs. Consequently these results validate the index/query
shape only; they are not a production p95 or an approved SLO.

Both measurements used an exact `project_id + region_code + [from,to)` predicate, stable
`fact_time DESC, rollout_id DESC` ordering, and `LIMIT 101` (maximum API page plus one).

## Retained committed-object technical fact read (not Activity)

Query source: `PostgresDashboardRepository.committed_objects`. This query is retained only as
BE21 technical/query-shape evidence; BR01 Activity does not expose it as a throughput bucket or
derive any byte metric from it. Proxy table:
`ingest.rollout_objects`; proxy cardinality: 1,000,000 rows.

| Plan | Main access path | Execution time | Buffers |
| --- | --- | ---: | --- |
| Before proposed index | Parallel sequential scan + top-N sort; 316,667 rows removed per worker | 38.189 ms | 2,210 hit, 10,272 read |
| After proposed index | Index Only Scan, 101 rows | 0.283 ms | 29 read |

The supporting index is
`(project_id, region_code, committed_at DESC, rollout_id DESC) INCLUDE
(data_package_id, file_size)`. This is evidence for the first statement in
`backend/migrations/dashboard/0001_dashboard_query_indexes.sql`.

## Collection-observation/coverage fact read

Query source: `PostgresDashboardRepository.collection_observations`. Proxy cardinality:
1,000,000 rollouts joined to 200,000 collection jobs. The measured query first applies scope,
time, stable order, and `LIMIT 101` to rollouts, then joins those bounded rows to jobs. Both join
sides retain exact project and region conditions.

| Plan | Main access path | Execution time | Buffers |
| --- | --- | ---: | --- |
| Before proposed index | Parallel sequential scan + top-N sort, then 101 primary-key job probes; 316,667 rows removed per worker | 40.242 ms | 3,529 hit, 9,357 read |
| After proposed index | Rollout Index Only Scan, then 101 primary-key job probes | 0.565 ms | 405 hit, 4 read |

The supporting index is
`(project_id, region_code, created_at DESC, rollout_id DESC) INCLUDE
(collection_job_id, robot_id)`. This is evidence for the second migration statement. An earlier
unbounded-join candidate regressed under the proxy distribution and was rejected; the repository
uses the bounded-subquery shape measured above.

## BR01 factual Activity and Pending structural EXPLAIN

`test_postgres_explain_business_feeds_keep_scope_range_order_and_limit` ran against a fresh
PostgreSQL 16.10 isolated database after applying the real security, ingest, quality, annotation,
publishing-lineage, and dashboard migrations. It executes `EXPLAIN (FORMAT JSON)` on the exact
production Activity and Pending SQL with:

- one principal, one project, one region, and `[from,to)` parameters in `requested_scope`;
- the complete keyset ordering and `LIMIT 101`;
- all four authorized pending source enums;
- sequential scans disabled only for this structural assertion, so the intended supporting
  access paths can be verified on a deliberately small fixture.

Both plans contain a top-level `Limit`. The Activity plan contains
`dashboard_rollout_objects_scope_committed_idx`; the Pending plan contains
`dashboard_quality_pending_scope_idx`. The same integration module separately asserts all BR01
event/pending/lineage indexes exist after fresh migration. This is query-shape evidence only:
`EXPLAIN` was intentionally not converted into a latency claim, and the small fixture is not a
production-volume benchmark.

## Budget and remaining validation

- Repository page size is capped at 100 and PostgreSQL statement time at 1.5 seconds. These are
  provisional technical resource guards, not product/SRE-approved budgets.
- No cache, materialized view, rollup, or aggregate table was added. Metric formula, freshness,
  and invalidation semantics are still unconfirmed.
- Production row counts, skew, table bloat, concurrent load, cold-cache behavior, replica lag,
  p95/p99 latency, and the target deployment hardware remain unmeasured.
- Coverage denominator and the unfrozen signal/episode/work snapshot metrics remain
  product-blocked, so no aggregate query plan can be honestly measured for them yet.
