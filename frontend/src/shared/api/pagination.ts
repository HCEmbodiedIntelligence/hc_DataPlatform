import { z } from 'zod';

export const pageInfoWireSchema = z
  .object({
    has_next_page: z.boolean(),
    has_previous_page: z.boolean(),
    start_cursor: z.string().nullable(),
    end_cursor: z.string().nullable(),
  })
  .strict();

export type PageInfo = z.infer<typeof pageInfoWireSchema>;

export interface Page<T> {
  items: T[];
  page_info: PageInfo;
  snapshot_at: string;
}

export function makePageSchema<T>(itemSchema: z.ZodType<T>): z.ZodType<Page<T>> {
  return z
    .object({
      items: z.array(itemSchema),
      page_info: pageInfoWireSchema,
      snapshot_at: z.iso.datetime({ offset: true }),
    })
    .strict();
}
