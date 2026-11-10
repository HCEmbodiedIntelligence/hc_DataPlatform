CREATE TABLE IF NOT EXISTS platform.object_store_prefix_aliases (
    bucket text NOT NULL,
    root_prefix text NOT NULL,
    logical_prefix text NOT NULL CHECK (logical_prefix <> ''),
    physical_prefix text NOT NULL CHECK (physical_prefix <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bucket, root_prefix, logical_prefix),
    UNIQUE (bucket, root_prefix, physical_prefix),
    CHECK (logical_prefix <> physical_prefix)
);
