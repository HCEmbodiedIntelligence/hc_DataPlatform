import { z } from "zod";

export const qualityDefaults = {
  timing: {minimum_frequency_ratio_risk: .9, minimum_frequency_ratio_reject: .75,
    minimum_coverage_ratio_risk: .98, minimum_coverage_ratio_reject: .8,
    maximum_gap_ns_risk: 100000000, maximum_gap_ns_reject: 1000000000,
    maximum_consecutive_missing_risk: 2, maximum_consecutive_missing_reject: 29,
    maximum_duplicate_timestamps_risk: 0, maximum_duplicate_timestamps_reject: null,
    maximum_backward_timestamps_reject: 0},
  completeness: {minimum_ratio_risk: .98, minimum_ratio_reject: .8},
  synchronization: {maximum_p95_offset_ns_risk: 20000000, maximum_p95_offset_ns_reject: 100000000},
  image: {black_luma_threshold: 5, maximum_black_frame_ratio_risk: .02, maximum_black_frame_ratio_reject: .5,
    maximum_repeated_frame_ratio_risk: .05, maximum_repeated_frame_ratio_reject: .5,
    maximum_corrupt_frame_ratio_risk: 0, maximum_corrupt_frame_ratio_reject: 0},
  action: {minimum_observation_count_risk: 1, minimum_observation_count_reject: 0,
    maximum_jump_risk: null, maximum_jump_reject: null, dimension_mismatch_is_reject: true},
  point_cloud: {minimum_point_count: null, maximum_point_count: null, maximum_empty_ratio_risk: 0,
    maximum_empty_ratio_reject: .2, maximum_abnormal_count_ratio_risk: 0, maximum_abnormal_count_ratio_reject: .5},
};


const ratio = z.number().min(0).max(1);
const count = z.number().int().nonnegative();
export const timingRules = z.object({
  minimum_frequency_ratio_risk: ratio, minimum_frequency_ratio_reject: ratio,
  minimum_coverage_ratio_risk: ratio, minimum_coverage_ratio_reject: ratio,
  maximum_gap_ns_risk: count, maximum_gap_ns_reject: count,
  maximum_consecutive_missing_risk: count, maximum_consecutive_missing_reject: count,
  maximum_duplicate_timestamps_risk: count, maximum_duplicate_timestamps_reject: count.nullable(),
  maximum_backward_timestamps_reject: count,
}).partial().transform(value => ({...qualityDefaults.timing, ...value}));
const imageRules = z.object({
  black_luma_threshold: z.number().min(0).max(255),
  maximum_black_frame_ratio_risk: ratio, maximum_black_frame_ratio_reject: ratio,
  maximum_repeated_frame_ratio_risk: ratio, maximum_repeated_frame_ratio_reject: ratio,
  maximum_corrupt_frame_ratio_risk: ratio, maximum_corrupt_frame_ratio_reject: ratio,
}).partial().transform(value => ({...qualityDefaults.image, ...value}));
const pointRules = z.object({
  minimum_point_count: count.nullable(), maximum_point_count: count.nullable(),
  maximum_empty_ratio_risk: ratio, maximum_empty_ratio_reject: ratio,
  maximum_abnormal_count_ratio_risk: ratio, maximum_abnormal_count_ratio_reject: ratio,
}).partial().transform(value => ({...qualityDefaults.point_cloud, ...value}));
export const qualityPolicySchema = z.object({
  missing_required: z.literal("reject").default("reject"), timing: timingRules.optional(),
  feature_timing: z.record(z.string(), timingRules).optional(),
  image: imageRules.optional(), feature_images: z.record(z.string(), imageRules).optional(),
  joint_limits: z.record(z.string(), z.tuple([z.number().finite(), z.number().finite()])).optional(),
  joint_out_of_range_is_reject: z.boolean().default(true),
  action: z.object({topic: z.literal("/action"), minimum_observation_count_risk: count,
    minimum_observation_count_reject: count, maximum_jump_risk: z.number().positive().nullable(),
    maximum_jump_reject: z.number().positive().nullable(), dimension_mismatch_is_reject: z.boolean()}).partial().transform(value => ({...qualityDefaults.action, topic: "/action" as const, ...value})).optional(),
  point_cloud: pointRules.optional(), feature_point_clouds: z.record(z.string(), pointRules).optional(),
  synchronization: z.object({maximum_p95_offset_ns_risk: count, maximum_p95_offset_ns_reject: count}).partial().transform(value => ({...qualityDefaults.synchronization, ...value})).optional(),
  completeness: z.object({topic: z.literal("__multimodal_step__"), minimum_ratio_risk: ratio,
    minimum_ratio_reject: ratio}).partial().transform(value => ({...qualityDefaults.completeness, topic: "__multimodal_step__" as const, ...value})).optional(),
});

