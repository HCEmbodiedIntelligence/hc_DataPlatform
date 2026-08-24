import { z } from 'zod';
import type { components } from '../../../shared/api/generated/platform';

export type DashboardActivityWire = components['schemas']['DashboardActivityResponse'];
export type DashboardSnapshotWire = components['schemas']['DashboardSnapshotResponse'];
export type DashboardPendingPageWire = components['schemas']['DashboardPendingItemsResponse'];

function generatedObject<T>(): z.ZodType<T> {
  return z.custom<T>(
    (value) => typeof value === 'object' && value !== null && !Array.isArray(value),
    'generated OpenAPI response must be an object',
  );
}

// Wire types come only from the generated production runtime schema. Domain adapters
// below enforce cross-field invariants that JSON Schema cannot express.
export const dashboardActivityWireSchema = generatedObject<DashboardActivityWire>();
export const dashboardSnapshotWireSchema = generatedObject<DashboardSnapshotWire>();
export const dashboardPendingPageWireSchema = generatedObject<DashboardPendingPageWire>();
