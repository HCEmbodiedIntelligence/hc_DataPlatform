import type { IngestScope } from "../../entities/data-source";

export function nativeRoot(scope: IngestScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/lerobot-imports`;
}

export interface NativeProgress {
  import_id: string;
  dataset_id: string;
  status: string;
  episode_count: number;
  ready: number;
  failed: number;
  last_error_code: string | null;
  updated_at: string;
  source_format: string;
  file_count: number;
  total_bytes: number;
}
