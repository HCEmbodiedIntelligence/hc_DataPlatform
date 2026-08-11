export type JobStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'UNKNOWN';

export interface AsyncJob {
  jobId: string;
  jobType: string;
  status: JobStatus;
  resourceType: string;
  resourceId: string;
  progress: unknown;
  resultRef: unknown;
  error: unknown;
  createdAt: string;
  updatedAt: string;
  resourceVersion: string;
}
