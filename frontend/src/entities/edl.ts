import { int64String, type Int64String } from '../shared/lib/bigint-string';

export type DecimalNanoseconds = Int64String & { readonly __unit: 'DecimalNanoseconds' };
export type SignedNanoseconds = Int64String & { readonly __unit: 'SignedNanoseconds' };

export function decimalNanoseconds(value: string): DecimalNanoseconds {
  const parsed = int64String(value);
  if (parsed.startsWith('-')) throw new RangeError('Nanoseconds must be non-negative');
  return parsed as DecimalNanoseconds;
}

export function signedNanoseconds(value: string): SignedNanoseconds {
  return int64String(value) as SignedNanoseconds;
}

export interface EdlOperationBase {
  readonly id: string;
  readonly sequenceNo: number;
  readonly enabled: boolean;
  readonly schemaVersion: '1';
}

export type EdlOperation =
  | (EdlOperationBase & {
      readonly type: 'TRIM';
      readonly startNs: DecimalNanoseconds;
      readonly endNs: DecimalNanoseconds;
    })
  | (EdlOperationBase & {
      readonly type: 'EXCLUDE_RANGE';
      readonly startNs: DecimalNanoseconds;
      readonly endNs: DecimalNanoseconds;
      readonly reason: string | null;
    })
  | (EdlOperationBase & { readonly type: 'SPLIT'; readonly atNs: DecimalNanoseconds })
  | (EdlOperationBase & {
      readonly type: 'TIME_OFFSET';
      readonly episodeStreamId: string;
      readonly offsetNs: SignedNanoseconds;
      readonly scope: 'EPISODE';
      readonly referenceStreamId: string | null;
    })
  | (EdlOperationBase & { readonly type: 'DISABLE_CHANNEL'; readonly episodeStreamId: string })
  | (EdlOperationBase & {
      readonly type: 'SET_METADATA';
      readonly patch: Readonly<Record<string, string | boolean | null>>;
    })
  | (EdlOperationBase & {
      readonly type: 'INVALIDATE_EPISODE';
      readonly reasonCode: string;
      readonly note: string | null;
    })
  | (EdlOperationBase & {
      readonly type: 'INVALID_MASK';
      readonly episodeStreamId: string | null;
      readonly startNs: DecimalNanoseconds;
      readonly endNs: DecimalNanoseconds;
      readonly reasonCode: string | null;
    });

export interface EditingDecisionList {
  readonly revision: string;
  readonly etag: string;
  readonly operationHash: string;
  readonly operations: readonly EdlOperation[];
  readonly updatedAt: string;
}

export interface TimeMappingSegment {
  readonly sourceStartNs: DecimalNanoseconds;
  readonly sourceEndNs: DecimalNanoseconds;
  readonly outputRevisionId: string;
  readonly outputStartNs: DecimalNanoseconds;
  readonly outputEndNs: DecimalNanoseconds;
}

export interface SourceToOutputMap {
  readonly mappingVersion: string;
  readonly segments: readonly [TimeMappingSegment, ...TimeMappingSegment[]];
}
