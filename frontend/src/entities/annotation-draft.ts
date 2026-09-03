import { z } from 'zod';

const id = z.string().min(1).max(128).regex(/^[A-Za-z0-9][A-Za-z0-9._:-]*$/);
const ns = z.string().regex(/^(0|[1-9][0-9]*)$/);
const attributes = z.record(z.string(), z.union([
  z.string().max(2048),
  z.number(),
  z.boolean(),
  z.null(),
  z.array(z.union([z.string(), z.number(), z.boolean(), z.null()])).max(128),
]));

const rangeAnchor = z.object({
  anchor_type: z.literal('TIME_RANGE'),
  coordinate_system: z.literal('REVISION_TIME_NS'),
  start_ns: ns,
  end_ns: ns,
}).superRefine((value, context) => {
  if (BigInt(value.start_ns) >= BigInt(value.end_ns)) context.addIssue({ code: 'custom', path: ['end_ns'], message: '结束时间必须晚于开始时间' });
});

const pointAnchor = z.object({
  anchor_type: z.literal('TIME_POINT'),
  coordinate_system: z.literal('REVISION_TIME_NS'),
  at_ns: ns,
});

const objectAnchor = z.object({
  anchor_type: z.literal('OBJECT_TRACK'),
  coordinate_system: z.literal('REVISION_TIME_NS'),
  stream_id: id,
  start_ns: ns,
  end_ns: ns,
  observations: z.array(z.object({
    at_ns: ns,
    geometry: z.union([
      z.object({ geometry_type: z.literal('BBOX_2D_NORMALIZED'), x: z.number().min(0).max(1), y: z.number().min(0).max(1), width: z.number().positive().max(1), height: z.number().positive().max(1) }),
      z.object({ geometry_type: z.literal('POINT_3D'), frame_id: id, x: z.number(), y: z.number(), z: z.number() }),
    ]),
    occluded: z.boolean(),
  })).min(1),
}).superRefine((value, context) => {
  if (BigInt(value.start_ns) >= BigInt(value.end_ns)) context.addIssue({ code: 'custom', path: ['end_ns'], message: '结束时间必须晚于开始时间' });
});

const entryBase = { annotation_id: id, label_code: id, attributes } as const;
export const annotationEntryWireSchema = z.discriminatedUnion('semantic_type', [
  z.object({ ...entryBase, semantic_type: z.literal('ACTION'), anchor: rangeAnchor }),
  z.object({ ...entryBase, semantic_type: z.literal('PHASE'), anchor: rangeAnchor }),
  z.object({ ...entryBase, semantic_type: z.literal('OBJECT'), anchor: objectAnchor }),
  z.object({ ...entryBase, semantic_type: z.literal('EVENT'), anchor: pointAnchor }),
  z.object({ ...entryBase, semantic_type: z.literal('KEYFRAME'), anchor: pointAnchor }),
]);

export type AnnotationEntryWire = z.infer<typeof annotationEntryWireSchema>;

export interface UnsupportedAnnotationEntry {
  readonly semanticType: 'UNSUPPORTED';
  readonly rawSemanticType: string;
  readonly raw: Readonly<Record<string, unknown>>;
}

export interface AnnotationDraft {
  readonly taskId: string;
  readonly revision: number;
  readonly state: 'ACTIVE' | 'FROZEN_SUBMITTED' | 'STALE_READ_ONLY' | 'UNKNOWN';
  readonly entries: readonly (AnnotationEntryWire | UnsupportedAnnotationEntry)[];
  readonly contentHash: string;
  readonly savedAt: string;
  readonly etag: string;
  readonly hasUnsupportedEntries: boolean;
}

export type AnnotationSaveState = 'CLEAN' | 'DIRTY' | 'SAVING' | 'SAVE_ERROR' | 'CONFLICT';
