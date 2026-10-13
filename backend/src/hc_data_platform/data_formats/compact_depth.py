"""Validate robot-quantized depth without inventing original sensor precision."""
import base64
import hashlib
import zlib
from dataclasses import dataclass

import numpy as np

from .depth_source import validate_profile
from .holobrain_depth import DEFAULT_PROFILE


@dataclass
class EncodedDepth:
    codes: np.ndarray
    flags: np.ndarray
    report: dict

    def error_report(self, profile=DEFAULT_PROFILE):
        if profile != DEFAULT_PROFILE:
            raise ValueError('compact depth quantization profile mismatch')
        return self.report


def validate_encoded_depth(codes, reference, source_profile, shape):
    validate_profile(source_profile)
    if (reference['schema_version'] != 'compact-depth-frame/v1'
            or reference['profile'] != DEFAULT_PROFILE.document()
            or reference['profile_sha256'] != DEFAULT_PROFILE.sha256):
        raise ValueError('unsupported compact depth profile')
    codes = np.asarray(codes)
    if codes.dtype != np.uint16 or list(codes.shape) != list(shape) or np.any(codes > 4095):
        raise ValueError('compact depth code shape/dtype/range mismatch')
    if hashlib.sha256(codes.astype('<u2').tobytes()).hexdigest() != reference['codes_sha256']:
        raise ValueError('compact depth code hash mismatch')
    decoder = zlib.decompressobj()
    flags_bytes = decoder.decompress(base64.b64decode(reference['flags_zlib_base64'], validate=True), codes.size + 1)
    if len(flags_bytes) != codes.size or not decoder.eof or decoder.unused_data:
        raise ValueError('compact depth flags size mismatch')
    flags = np.frombuffer(flags_bytes, dtype=np.uint8).reshape(codes.shape)
    if np.any(flags > 7) or np.any(((flags & 6) != 0) & ((flags & 1) == 0)) or np.any((flags & 6) == 6):
        raise ValueError('invalid compact depth flags')
    valid, low, high = (flags & 1) != 0, (flags & 2) != 0, (flags & 4) != 0
    if np.any(codes[~valid] != 0) or np.any(codes[low] != 0) or np.any(codes[high] != 4095):
        raise ValueError('compact depth flags disagree with codes')
    report = dict(reference['quantization_report'])
    counts = {'pixels': codes.size, 'valid_pixels': int(valid.sum()),
              'invalid_pixels': int((~valid).sum()), 'saturated_low_pixels': int(low.sum()),
              'saturated_high_pixels': int(high.sum()), 'unclipped_pixels': int((valid & ~low & ~high).sum())}
    if any(report.get(k) != v for k, v in counts.items()) or report.get('half_cell_bound_passed') is not True:
        raise ValueError('compact depth evidence counts mismatch')
    for key in ('unclipped_max_error_m', 'unclipped_p50_error_m', 'unclipped_p95_error_m',
                'unclipped_p99_error_m', 'clipped_max_error_m'):
        value = report[key]
        if value is not None and (not np.isfinite(value) or value < 0):
            raise ValueError('invalid compact depth error evidence')
    return EncodedDepth(codes, flags, report)
