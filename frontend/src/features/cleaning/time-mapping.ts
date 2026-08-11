import {
  decimalNanoseconds,
  type DecimalNanoseconds,
  type SourceToOutputMap,
  type TimeMappingSegment,
} from '../../entities/edl';
import {
  int64FromBigInt,
  int64ToBigInt,
  type Int64String,
} from '../../shared/lib/bigint-string';

function valueOf(value: DecimalNanoseconds): bigint {
  return int64ToBigInt(value as Int64String);
}

function decimal(value: bigint): DecimalNanoseconds {
  return int64FromBigInt(value) as DecimalNanoseconds;
}

function assertHalfOpen(start: DecimalNanoseconds, end: DecimalNanoseconds, label: string): void {
  if (valueOf(start) >= valueOf(end)) throw new RangeError(`${label} must be a non-empty [start,end) range`);
}

export function validateSourceToOutputMap(map: SourceToOutputMap): void {
  if (map.segments.length === 0) throw new RangeError('Time mapping requires at least one segment');
  for (const segment of map.segments) {
    assertHalfOpen(segment.sourceStartNs, segment.sourceEndNs, 'source mapping');
    assertHalfOpen(segment.outputStartNs, segment.outputEndNs, 'output mapping');
    const sourceDuration = valueOf(segment.sourceEndNs) - valueOf(segment.sourceStartNs);
    const outputDuration = valueOf(segment.outputEndNs) - valueOf(segment.outputStartNs);
    if (sourceDuration !== outputDuration) {
      throw new RangeError('Each mapping segment must preserve local duration');
    }
  }
}

export interface OutputTimePosition {
  readonly outputRevisionId: string;
  readonly outputNs: DecimalNanoseconds;
  readonly segment: TimeMappingSegment;
}

export function sourceTimeToOutputs(
  map: SourceToOutputMap,
  sourceNs: DecimalNanoseconds,
): readonly OutputTimePosition[] {
  validateSourceToOutputMap(map);
  const source = valueOf(sourceNs);
  return map.segments.flatMap((segment) => {
    const start = valueOf(segment.sourceStartNs);
    const end = valueOf(segment.sourceEndNs);
    if (source < start || source >= end) return [];
    return [{
      outputRevisionId: segment.outputRevisionId,
      outputNs: decimal(valueOf(segment.outputStartNs) + source - start),
      segment,
    }];
  });
}

export function outputTimeToSource(
  map: SourceToOutputMap,
  outputRevisionId: string,
  outputNs: DecimalNanoseconds,
): DecimalNanoseconds | null {
  validateSourceToOutputMap(map);
  const output = valueOf(outputNs);
  const segment = map.segments.find((candidate) =>
    candidate.outputRevisionId === outputRevisionId &&
    output >= valueOf(candidate.outputStartNs) &&
    output < valueOf(candidate.outputEndNs));
  if (!segment) return null;
  return decimal(valueOf(segment.sourceStartNs) + output - valueOf(segment.outputStartNs));
}

export interface OutputRangeMapping {
  readonly outputRevisionId: string;
  readonly sourceStartNs: DecimalNanoseconds;
  readonly sourceEndNs: DecimalNanoseconds;
  readonly outputStartNs: DecimalNanoseconds;
  readonly outputEndNs: DecimalNanoseconds;
}

export function sourceRangeToOutputs(
  map: SourceToOutputMap,
  sourceStartNs: DecimalNanoseconds,
  sourceEndNs: DecimalNanoseconds,
): readonly OutputRangeMapping[] {
  assertHalfOpen(sourceStartNs, sourceEndNs, 'requested source range');
  validateSourceToOutputMap(map);
  const requestedStart = valueOf(sourceStartNs);
  const requestedEnd = valueOf(sourceEndNs);
  return map.segments.flatMap((segment) => {
    const segmentStart = valueOf(segment.sourceStartNs);
    const segmentEnd = valueOf(segment.sourceEndNs);
    const intersectionStart = requestedStart > segmentStart ? requestedStart : segmentStart;
    const intersectionEnd = requestedEnd < segmentEnd ? requestedEnd : segmentEnd;
    if (intersectionStart >= intersectionEnd) return [];
    const offset = valueOf(segment.outputStartNs) - segmentStart;
    return [{
      outputRevisionId: segment.outputRevisionId,
      sourceStartNs: decimal(intersectionStart),
      sourceEndNs: decimal(intersectionEnd),
      outputStartNs: decimal(intersectionStart + offset),
      outputEndNs: decimal(intersectionEnd + offset),
    }];
  });
}

export function timeMappingSegment(input: {
  readonly sourceStartNs: string;
  readonly sourceEndNs: string;
  readonly outputRevisionId: string;
  readonly outputStartNs: string;
  readonly outputEndNs: string;
}): TimeMappingSegment {
  return {
    sourceStartNs: decimalNanoseconds(input.sourceStartNs),
    sourceEndNs: decimalNanoseconds(input.sourceEndNs),
    outputRevisionId: input.outputRevisionId,
    outputStartNs: decimalNanoseconds(input.outputStartNs),
    outputEndNs: decimalNanoseconds(input.outputEndNs),
  };
}

