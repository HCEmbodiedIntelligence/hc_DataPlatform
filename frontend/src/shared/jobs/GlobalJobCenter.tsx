import { useAsyncJob } from './use-async-job';
import { useJobCenterStore } from './job-center-store';

function JobCenterItem({ jobId }: { jobId: string }) {
  const job = useAsyncJob(jobId);
  return (
    <li className="global-job-center__item">
      <span>{job.data?.jobType ?? '任务'}</span>
      <span aria-live="polite">{job.data?.status ?? '加载中'}</span>
      {job.connectionStatus === 'polling' ? <span>实时连接中断，正在轮询</span> : null}
    </li>
  );
}

export function GlobalJobCenter() {
  const jobIds = useJobCenterStore((state) => state.jobIds);
  return (
    <section className="global-job-center" aria-label="全局任务中心">
      <h2>任务中心</h2>
      {jobIds.length === 0 ? (
        <p>暂无运行中的任务</p>
      ) : (
        <ul>
          {jobIds.map((jobId) => (
            <JobCenterItem key={jobId} jobId={jobId} />
          ))}
        </ul>
      )}
    </section>
  );
}
