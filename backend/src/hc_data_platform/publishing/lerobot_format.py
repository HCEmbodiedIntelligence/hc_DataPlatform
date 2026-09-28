"""Shared official LeRobot task metadata layout for primary packages and exports."""

import pyarrow as pa

from .service import canonical_json_bytes


def make_task_table(tasks):
    tasks_table = pa.table(
        {"task_index": pa.array(range(len(tasks)), type=pa.int64()), "task": tasks}
    )
    pandas_metadata = {
        "index_columns": ["task"],
        "column_indexes": [],
        "columns": [
            {
                "name": "task_index",
                "field_name": "task_index",
                "pandas_type": "int64",
                "numpy_type": "int64",
                "metadata": None,
            },
            {
                "name": "task",
                "field_name": "task",
                "pandas_type": "unicode",
                "numpy_type": "object",
                "metadata": None,
            },
        ],
        "creator": {"library": "hc-data-platform", "version": "2"},
        "pandas_version": "2.0.0",
    }
    tasks_table = tasks_table.replace_schema_metadata(
        {b"pandas": canonical_json_bytes(pandas_metadata)}
    )
    return tasks_table
