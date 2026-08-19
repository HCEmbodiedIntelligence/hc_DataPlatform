import { beforeEach, describe, expect, it, vi } from 'vitest';
import { request } from '../../shared/api/http-client';
import type { paths } from '../../shared/api/generated/storage';
import { parseWire } from '../../shared/api/validate';
import {
  capacityInventoryFactWireSchema,
  capacitySnapshotWireSchema,
  getCapacitySnapshot,
  type CapacitySnapshot,
} from './capacity-api';

vi.mock('../../shared/api/http-client', () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope = {
  organizationId: '',
  projectId: 'project-a',
  regionCode: 'cn-test',
} as const;

type ForbiddenStoragePath = Extract<
  keyof paths,
  `${string}simulation${string}` | `${string}dry-run${string}` | `${string}impact${string}`
>;
const runtimeContractHasNoForbiddenStoragePath: ForbiddenStoragePath extends never ? true : false = true;

const validSnapshot: CapacitySnapshot = {
  snapshot_id: 'snapshot-1',
  project_id: 'project-a',
  observed_at: '2026-08-17T02:30:00Z',
  physical_total_bytes: '310',
  physical_instance_count: 6,
  candidate_business_total_bytes: '200',
  candidate_logical_object_count: 4,
  categories: [
    { category: 'RAW', candidate_bytes: '100', logical_object_count: 1 },
    { category: 'ANNOTATION_COMPLETE', candidate_bytes: '50', logical_object_count: 1 },
    { category: 'PENDING_ANNOTATION', candidate_bytes: '30', logical_object_count: 1 },
    { category: 'ISSUE_DATA', candidate_bytes: '20', logical_object_count: 1 },
  ],
  reconciliation: {
    replica_overhead_bytes: '100',
    replica_instance_count: 1,
    temporary_bytes: '10',
    temporary_instance_count: 1,
    duplicate_inventory_rows_ignored: 1,
    formula: 'physical_total_bytes = candidate_business_total_bytes + replica_overhead_bytes + temporary_bytes',
    balanced: true,
  },
};

describe('formal storage capacity wire contract', () => {
  beforeEach(() => requestMock.mockReset());

  it('contains only the public runtime paths and accepts exact dual-metric reconciliation', () => {
    expect(runtimeContractHasNoForbiddenStoragePath).toBe(true);
    expect(parseWire(capacitySnapshotWireSchema, validSnapshot, { endpoint: 'test' })).toEqual(validSnapshot);
  });

  it('fails closed on category order, arithmetic, and temporary-sample classification drift', () => {
    expect(() => parseWire(capacitySnapshotWireSchema, {
      ...validSnapshot,
      physical_total_bytes: '311',
      categories: [validSnapshot.categories[1], validSnapshot.categories[0], ...validSnapshot.categories.slice(2)],
    }, { endpoint: 'test' })).toThrow('服务端响应与当前合同不匹配');

    expect(() => parseWire(capacityInventoryFactWireSchema, {
      snapshot_id: 'snapshot-1',
      project_id: 'project-a',
      physical_instance_id: 'temp-1',
      logical_object_id: null,
      physical_bytes: '10',
      disposition: 'TEMPORARY',
      business_category: 'RAW',
      object_role: 'OTHER',
      observed_at: '2026-08-17T02:30:00Z',
    }, { endpoint: 'test' })).toThrow('服务端响应与当前合同不匹配');
  });

  it('binds the formal capacity read to one immutable project/region scope', async () => {
    requestMock.mockResolvedValue(validSnapshot);
    await expect(getCapacitySnapshot(scope)).resolves.toEqual(validSnapshot);
    expect(requestMock).toHaveBeenCalledWith({
      method: 'GET',
      path: '/projects/project-a/storage/capacity',
      scope,
    });
  });

  it('reports a cross-project capacity response as a contract mismatch', async () => {
    requestMock.mockResolvedValue({ ...validSnapshot, project_id: 'project-b' });
    await expect(getCapacitySnapshot(scope)).rejects.toMatchObject({
      code: 'CONTRACT_MISMATCH',
      retryable: false,
    });
  });
});
