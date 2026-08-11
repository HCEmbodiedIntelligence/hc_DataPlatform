import {
  decimalNanoseconds,
  signedNanoseconds,
  type EdlOperation,
  type EditingDecisionList,
  type SourceToOutputMap,
} from '../../../entities/edl';
import { timeMappingSegment, validateSourceToOutputMap } from '../time-mapping';
import type { CleaningEdlWire, CleaningOperationWire } from './edl.schemas';

export function adaptCleaningOperation(wire: CleaningOperationWire): EdlOperation {
  const common = {
    id: wire.id,
    sequenceNo: wire.sequence_no,
    enabled: wire.enabled,
    schemaVersion: '1' as const,
  };
  switch (wire.type) {
    case 'TRIM':
      return { ...common, type: wire.type, startNs: decimalNanoseconds(wire.start_ns), endNs: decimalNanoseconds(wire.end_ns) };
    case 'EXCLUDE_RANGE':
      return { ...common, type: wire.type, startNs: decimalNanoseconds(wire.start_ns), endNs: decimalNanoseconds(wire.end_ns), reason: wire.reason };
    case 'SPLIT':
      return { ...common, type: wire.type, atNs: decimalNanoseconds(wire.at_ns) };
    case 'TIME_OFFSET':
      return { ...common, type: wire.type, episodeStreamId: wire.episode_stream_id, offsetNs: signedNanoseconds(wire.offset_ns), scope: wire.scope, referenceStreamId: wire.reference_stream_id };
    case 'DISABLE_CHANNEL':
      return { ...common, type: wire.type, episodeStreamId: wire.episode_stream_id };
    case 'SET_METADATA':
      return { ...common, type: wire.type, patch: wire.patch };
    case 'INVALIDATE_EPISODE':
      return { ...common, type: wire.type, reasonCode: wire.reason_code, note: wire.note };
    case 'INVALID_MASK':
      return { ...common, type: wire.type, episodeStreamId: wire.episode_stream_id, startNs: decimalNanoseconds(wire.start_ns), endNs: decimalNanoseconds(wire.end_ns), reasonCode: wire.reason_code };
  }
}

export function serializeCleaningOperation(operation: EdlOperation): CleaningOperationWire {
  const common = {
    id: operation.id,
    sequence_no: operation.sequenceNo,
    enabled: operation.enabled,
    schema_version: '1' as const,
  };
  switch (operation.type) {
    case 'TRIM':
      return { ...common, type: operation.type, start_ns: operation.startNs, end_ns: operation.endNs };
    case 'EXCLUDE_RANGE':
      return { ...common, type: operation.type, start_ns: operation.startNs, end_ns: operation.endNs, reason: operation.reason };
    case 'SPLIT':
      return { ...common, type: operation.type, at_ns: operation.atNs };
    case 'TIME_OFFSET':
      return { ...common, type: operation.type, episode_stream_id: operation.episodeStreamId, offset_ns: operation.offsetNs, scope: operation.scope, reference_stream_id: operation.referenceStreamId };
    case 'DISABLE_CHANNEL':
      return { ...common, type: operation.type, episode_stream_id: operation.episodeStreamId };
    case 'SET_METADATA':
      return { ...common, type: operation.type, patch: operation.patch };
    case 'INVALIDATE_EPISODE':
      return { ...common, type: operation.type, reason_code: operation.reasonCode, note: operation.note };
    case 'INVALID_MASK':
      return { ...common, type: operation.type, episode_stream_id: operation.episodeStreamId, start_ns: operation.startNs, end_ns: operation.endNs, reason_code: operation.reasonCode };
  }
}

export function adaptCleaningEdl(wire: CleaningEdlWire): EditingDecisionList {
  return {
    revision: wire.edl_revision,
    etag: wire.etag,
    operationHash: wire.operation_hash,
    operations: wire.operations.map(adaptCleaningOperation),
    updatedAt: wire.updated_at,
  };
}

export function adaptSourceToOutputMap(wire: {
  readonly mapping_version: string;
  readonly segments: readonly {
    readonly source_start_ns: string;
    readonly source_end_ns: string;
    readonly output_revision_id: string;
    readonly output_start_ns: string;
    readonly output_end_ns: string;
  }[];
}): SourceToOutputMap {
  if (wire.segments.length === 0) throw new Error('TIME_MAPPING_EMPTY');
  const map: SourceToOutputMap = {
    mappingVersion: wire.mapping_version,
    segments: wire.segments.map((segment) => timeMappingSegment({
      sourceStartNs: segment.source_start_ns,
      sourceEndNs: segment.source_end_ns,
      outputRevisionId: segment.output_revision_id,
      outputStartNs: segment.output_start_ns,
      outputEndNs: segment.output_end_ns,
    })) as unknown as SourceToOutputMap['segments'],
  };
  validateSourceToOutputMap(map);
  return map;
}
