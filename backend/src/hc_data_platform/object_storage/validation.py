from collections.abc import Sequence

from hc_data_platform.core.errors import problem

from .checksums import normalize_etag
from .models import CompletedPartReference, MultipartPart


def validate_part_number(part_number: int) -> None:
    if not 1 <= part_number <= 10_000:
        raise ValueError("part_number must be between 1 and 10000")


def validate_completion_parts(
    supplied: Sequence[CompletedPartReference],
    actual: Sequence[MultipartPart],
) -> None:
    numbers = [part.part_number for part in supplied]
    if not numbers:
        raise problem(
            status=409,
            code="MULTIPART_EMPTY",
            title="Multipart upload is empty",
            detail="At least one uploaded part is required.",
        )
    if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
        raise problem(
            status=422,
            code="PARTS_NOT_SORTED",
            title="Multipart completion parts are not sorted",
            detail="Parts must be unique and sorted by ascending part_number.",
        )
    supplied_pairs = [(part.part_number, normalize_etag(part.etag)) for part in supplied]
    actual_pairs = [(part.part_number, normalize_etag(part.etag)) for part in actual]
    if supplied_pairs != actual_pairs:
        raise problem(
            status=409,
            code="MULTIPART_PARTS_MISMATCH",
            title="Multipart part list does not match storage",
            detail="The supplied part numbers and ETags must exactly match uploaded parts.",
            details={
                "supplied_part_numbers": numbers,
                "uploaded_part_numbers": [part.part_number for part in actual],
            },
        )
