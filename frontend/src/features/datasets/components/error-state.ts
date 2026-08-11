import { isDomainError } from '../../../shared/api/domain-error';
import type { DatasetRegionState } from './RegionState';

export function datasetRegionStateForError(error: unknown): DatasetRegionState {
  if (!isDomainError(error)) return 'fatal-error';
  switch (error.code) {
    case 'FORBIDDEN':
    case 'UNAUTHENTICATED':
      return 'forbidden';
    case 'NOT_FOUND':
      return 'not-found';
    case 'GONE':
      return 'gone';
    case 'VERSION_CONFLICT':
    case 'PRECONDITION_FAILED':
      return 'conflict';
    case 'RATE_LIMITED':
      return 'rate-limited';
    case 'NETWORK_ERROR':
      return typeof navigator !== 'undefined' && !navigator.onLine ? 'offline' : 'reconnecting';
    case 'CONTRACT_MISMATCH':
      return 'contract-mismatch';
    default:
      return 'fatal-error';
  }
}
