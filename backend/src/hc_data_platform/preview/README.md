# Historical preview schema

The `preview` PostgreSQL migrations remain immutable for checksum compatibility. The HLS,
session, cold-generation, and TTL/LRU runtime was retired. Canonical camera media now lives in
`hc_data_platform.aligned_media`; migration `preview/0004_canonical_aligned_media.sql` creates an
independent `aligned_media` schema without changing migrations 0001–0003. Runtime code does not
read or write the historical `preview` tables.
