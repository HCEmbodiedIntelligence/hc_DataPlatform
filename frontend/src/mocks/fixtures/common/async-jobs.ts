import type { AsyncJob, JobStatus } from '../../../shared/jobs/use-async-job';
import { FIXTURE_BASE_TIME } from './scope';

const offsets: Record<JobStatus, string> = {
  QUEUED: FIXTURE_BASE_TIME,
  RUNNING: '2026-08-05T08:00:01Z',
  SUCCEEDED: '2026-08-05T08:00:02Z',
  FAILED: '2026-08-05T08:00:03Z',
  CANCELLED: '2026-08-05T08:00:04Z',
  UNKNOWN: '2026-08-05T08:00:05Z',
};

const versions: Record<JobStatus, string> = {
  QUEUED: '1',
  RUNNING: '2',
  SUCCEEDED: '3',
  FAILED: '3',
  CANCELLED: '3',
  UNKNOWN: '1',
};

function makeJob(status: JobStatus): AsyncJob {
  return Object.freeze({
    jobId: `job_fx_${status.toLowerCase()}`,
    jobType: 'UPLOAD_VERIFY',
    status,
    resourceType: 'upload_session',
    resourceId: 'upl_fx_01',
    progress: status === 'RUNNING' ? { completed: '50', total: '100' } : null,
    resultRef: status === 'SUCCEEDED' ? { type: 'dataset_version', id: 'version_fx_01' } : null,
    error: status === 'FAILED' ? { code: 'FIXTURE_JOB_FAILED', requestId: 'req_fx_job_failed' } : null,
    createdAt: FIXTURE_BASE_TIME,
    updatedAt: offsets[status],
    resourceVersion: versions[status],
  });
}

export const fixtureAsyncJobs: Readonly<Record<JobStatus, AsyncJob>> = Object.freeze({
  QUEUED: makeJob('QUEUED'),
  RUNNING: makeJob('RUNNING'),
  SUCCEEDED: makeJob('SUCCEEDED'),
  FAILED: makeJob('FAILED'),
  CANCELLED: makeJob('CANCELLED'),
  UNKNOWN: makeJob('UNKNOWN'),
});
