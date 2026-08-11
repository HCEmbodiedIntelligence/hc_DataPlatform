import { z } from 'zod';

const id = z.string().min(1).max(160);
const instant = z.string().datetime({ offset: true });
const uint64 = z.string().regex(/^(0|[1-9]\d*)$/).refine((value) => BigInt(value) <= 18_446_744_073_709_551_615n);

export const dashboardScopeWireSchema = z.object({
  organization_id: id,
  project_id: id,
  region_code: id,
}).strict();

const envelope = <T extends z.ZodType>(data: T) => z.object({
  data,
  scope: dashboardScopeWireSchema,
  request_id: id,
  contract_version: z.literal('v1'),
}).strict();

export const dashboardActivityQueryWireSchema = z.object({
  from: instant,
  to: instant,
  timezone: z.string().min(1).max(128),
}).strict().superRefine((value, context) => {
  if (Date.parse(value.from) >= Date.parse(value.to)) {
    context.addIssue({ code: 'custom', message: 'from must be before to' });
  }
});

export const dashboardActivityWireSchema = envelope(z.object({
  from: instant,
  to: instant,
  timezone: z.string().min(1).max(128),
  as_of: instant,
  uploads: z.object({
    accepted_unique_bytes: uint64,
    succeeded_count: uint64,
    terminal_count: uint64,
    buckets: z.array(z.object({
      start: instant,
      end: instant,
      accepted_unique_bytes: uint64,
      failed_count: uint64,
    }).strict()),
  }).strict(),
}).strict());

export const dashboardSnapshotWireSchema = envelope(z.object({
  timezone: z.string().min(1).max(128),
  as_of: instant,
  storage: z.object({
    data_physical_bytes: uint64,
    by_role: z.array(z.object({ role: z.string().min(1), bytes: uint64 }).strict()),
    history: z.array(z.object({
      month: z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/),
      standard_bytes: uint64,
      ia_bytes: uint64,
      archive_bytes: uint64,
      data_physical_bytes: uint64,
    }).strict()).max(6),
  }).strict(),
  episodes: z.object({
    uploaded_count: uint64,
    validated_count: uint64,
    viewable_count: uint64,
  }).strict(),
  work: z.object({
    open_manual_issue_count: uint64,
    pending_review_version_count: uint64,
    returned_actionable_draft_count: uint64,
    returned_scope: z.literal('ACTIONABLE_SUCCESSOR'),
    active_cleaning_draft_count: uint64,
    draft_scope: z.literal('ACTIONABLE'),
  }).strict(),
}).strict());

export const dashboardCoverageWireSchema = envelope(z.object({
  timezone: z.string().min(1).max(128),
  as_of: instant,
  robot_groups: z.array(z.object({ id, name: z.string().min(1).max(120), order: z.number().int().nonnegative() }).strict()),
  tasks: z.array(z.object({ id, name: z.string().min(1).max(120), order: z.number().int().nonnegative() }).strict()),
  cells: z.array(z.object({
    robot_group_id: id,
    task_id: id,
    ratio: z.number().finite().min(0).max(1).nullable(),
    numerator: uint64,
    denominator: uint64,
  }).strict()),
}).strict());

const pendingBase = {
  item_id: id,
  type: z.string().min(1).max(64),
  title: z.string().min(1).max(120),
  summary: z.string().max(500).nullable(),
  status: z.string().min(1).max(64),
  priority: z.string().min(1).max(32),
  updated_at: instant,
};

export const dashboardPendingItemWireSchema = z.object({
  ...pendingBase,
  upload_id: id.optional(),
  verification_run_id: id.optional(),
  failure_count: uint64.optional(),
  issue_id: id.optional(),
  version_id: id.optional(),
  episode_id: id.optional(),
  draft_id: id.optional(),
  preview_status: z.string().optional(),
  target: z.object({
    kind: z.string().min(1),
    dataset_id: id.optional(),
    version_id: id.optional(),
    successor_draft_id: id.optional(),
    review_decision_id: id.optional(),
    finding_id: id.optional(),
    object_role: z.string().optional(),
    storage_class: z.string().optional(),
    policy_id: id.optional(),
    execution_id: id.optional(),
  }).strict().optional(),
  failure_code: z.string().nullable().optional(),
}).strict();

export const dashboardPendingPageWireSchema = envelope(z.object({
  as_of: instant,
  total_count: uint64,
  items: z.array(dashboardPendingItemWireSchema),
  page_info: z.object({
    has_next_page: z.boolean(),
    has_previous_page: z.boolean(),
    start_cursor: z.string().min(1).nullable(),
    end_cursor: z.string().min(1).nullable(),
  }).strict(),
  snapshot_at: instant,
}).strict());

export type DashboardActivityWire = z.infer<typeof dashboardActivityWireSchema>;
export type DashboardSnapshotWire = z.infer<typeof dashboardSnapshotWireSchema>;
export type DashboardCoverageWire = z.infer<typeof dashboardCoverageWireSchema>;
export type DashboardPendingPageWire = z.infer<typeof dashboardPendingPageWireSchema>;
