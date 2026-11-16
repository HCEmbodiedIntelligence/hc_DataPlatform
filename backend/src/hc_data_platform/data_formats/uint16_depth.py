"""Exact frame statistics for a 16-bit sensor with millimetre units."""
from functools import lru_cache

import numpy as np

from .compact_depth import EncodedDepth
from .holobrain_depth import DEFAULT_PROFILE, dequantize, quantize


@lru_cache(maxsize=1)
def _millimetre_table():
    raw = np.arange(65536, dtype=np.uint16).reshape(256, 256)
    reference = quantize(raw, 0.001, DEFAULT_PROFILE)
    # Checks the half-cell bound for every possible source measurement once.
    report = reference.error_report(DEFAULT_PROFILE)
    errors = np.abs(dequantize(reference.codes).astype(np.float64) - reference.physical_m).ravel()
    flags = reference.flags.ravel()
    interior = np.flatnonzero(flags == 1)
    clipped = np.flatnonzero((flags & 6) != 0)
    return (
        reference.codes.ravel(), flags, errors, report,
        interior[np.argsort(errors[interior])], clipped[np.argsort(errors[clipped])],
        np.flatnonzero(flags & 2), np.flatnonzero(flags & 4),
    )


def millimetre_evidence(pixels):
    codes, flags, errors, template, interior, clipped, low, high = _millimetre_table()
    counts = np.bincount(pixels.ravel(), minlength=65536)
    # In error order, a bin occupies exactly as many ranks as its pixel count.
    cumulative = np.cumsum(counts[interior])
    n = int(cumulative[-1])
    p50 = p95 = p99 = max_error = None
    if n:
        ranks = np.array([0.5, 0.95, 0.99, 1.0]) * (n - 1)
        left = np.floor(ranks).astype(np.intp)
        right = np.ceil(ranks).astype(np.intp)
        lo = errors[interior[np.searchsorted(cumulative, left, side="right")]]
        hi = errors[interior[np.searchsorted(cumulative, right, side="right")]]
        fraction = ranks - left
        # Match NumPy's linear interpolation, including rounding near the upper endpoint.
        delta = hi - lo
        quantiles = np.where(fraction < 0.5, lo + delta * fraction, hi - delta * (1 - fraction))
        p50, p95, p99, max_error = map(float, quantiles)
    clipped_present = np.flatnonzero(counts[clipped])
    clipped_max = float(errors[clipped[clipped_present[-1]]]) if clipped_present.size else None
    invalid = int(counts[0])
    report = dict(
        template, pixels=pixels.size, valid_pixels=pixels.size-invalid, invalid_pixels=invalid,
        saturated_low_pixels=int(counts[low].sum()), saturated_high_pixels=int(counts[high].sum()),
        unclipped_pixels=n, unclipped_max_error_m=max_error, unclipped_p50_error_m=p50,
        unclipped_p95_error_m=p95, unclipped_p99_error_m=p99, clipped_max_error_m=clipped_max,
    )
    return EncodedDepth(codes[pixels], flags[pixels], report)
