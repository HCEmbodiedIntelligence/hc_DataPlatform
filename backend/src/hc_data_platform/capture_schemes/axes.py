"""Named source-clock axes may be permuted; joint/pose semantics remain strict."""

SOURCE_AXES = frozenset(
    {
        "observation.source_time",
        "observation.source_age",
        "observation.source_available",
        "observation.source_valid",
    }
)


def source_axis_order(name, expected, observed):
    """Return an exact bijection only for known one-dimensional source fields."""
    names, original = expected.get("names"), observed.get("names")
    if (
        name not in SOURCE_AXES
        or not isinstance(names, list)
        or not names
        or not isinstance(original, list)
        or not all(isinstance(n, str) for n in original)
        or len(set(names)) != len(names)
        or len(set(original)) != len(original)
        or set(names) != set(original)
        or names == original
        or expected.get("shape") != [len(names)]
        or observed.get("shape") != [len(original)]
    ):
        return None
    return [original.index(n) for n in names]
