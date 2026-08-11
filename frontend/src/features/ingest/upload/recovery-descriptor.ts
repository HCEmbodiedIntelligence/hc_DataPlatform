import { z } from 'zod';

export const uploadRecoveryDescriptorSchema = z.object({
  schemaVersion: z.literal(1),
  uploadId: z.string().min(1),
  uploadObjectId: z.string().min(1),
  clientObjectId: z.string().min(1),
  fileFingerprint: z.object({ sizeBytes: z.string().regex(/^(0|[1-9]\d*)$/), lastModifiedAt: z.string().datetime({ offset: true }), sampleSha256: z.string().regex(/^[a-f0-9]{64}$/).nullable() }).strict(),
  confirmedPartNumbers: z.array(z.string().regex(/^[1-9]\d*$/)),
  descriptorVersion: z.string().regex(/^[1-9]\d*$/),
  updatedAt: z.string().datetime({ offset: true }),
}).strict();

export type UploadRecoveryDescriptor = z.infer<typeof uploadRecoveryDescriptorSchema>;

/** Serialization is allowlist-only: no STS, URL, Bucket, object key, multipart ID, or local path exists in the type. */
export function serializeRecoveryDescriptor(value: UploadRecoveryDescriptor): string {
  return JSON.stringify(uploadRecoveryDescriptorSchema.parse(value));
}
