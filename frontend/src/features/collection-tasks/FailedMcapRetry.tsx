import { useQueries, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Modal, Radio, Space, Table } from "antd";
import { useRef, useState } from "react";
import { request } from "../../shared/api/http-client";
import { nativeRoot } from "../ingest/processing/lerobot-processing";
import { captureError } from "../capture-schemes/presentation";
import type { CollectionTaskScope } from "./api";

interface FailedFile {
  raw_source_id: string;
  processing_id: string;
  source_path: string;
  recording_id: string | null;
  stage: "PREPARATION" | "QUALITY" | "EPISODE_QUALITY" | "INGEST";
}
interface Candidates {
  plan: { revision: number; scheme: { scheme_id: string; version: number } } | null;
  items: FailedFile[];
}

export interface RetryTask {
  taskId: string;
  name?: string;
  issueKeys?: ReadonlySet<string>;
}
type RetryScope = { taskId: string; targets?: never } | { taskId?: never; targets: RetryTask[] };

export function FailedMcapRetry({ scope, taskId, targets }: { scope: CollectionTaskScope } & RetryScope) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"full" | "qc">("full");
  const [selected, setSelected] = useState<string[]>([]);
  const [working, setWorking] = useState(false);
  const [summary, setSummary] = useState<{ submitted: number; errors: string[] } | null>(null);
  const submitting = useRef(false);
  const root = nativeRoot(scope);
  const tasks: RetryTask[] = targets ?? [{ taskId }];
  const lists = useQueries({ queries: tasks.map(task => ({
    queryKey: ["failed-mcap", scope.organizationId, scope.projectId, scope.regionCode, task.taskId],
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }: { signal: AbortSignal }) => request<Candidates>({
      scope, signal, method: "GET", path: `${root}/tasks/${encodeURIComponent(task.taskId)}/failed-mcap`,
    }),
  })) });
  const fetching = lists.some(list => list.isFetching);
  const files = tasks.flatMap((task, i) => {
    const result = lists[i];
    if (!result || result.isError) return [];
    return (result.data?.items ?? []).filter(file =>
      (!task.issueKeys || task.issueKeys.has(file.processing_id) || (!!file.recording_id && task.issueKeys.has(file.recording_id))) &&
      (mode === "full" || (file.stage === "QUALITY" && file.recording_id))
    ).map(file => ({ ...file, task, plan: result.data?.plan }));
  });
  const selectable = files.filter(file => mode === "qc" || file.plan);
  const plan = tasks.length === 1 ? lists[0]?.data?.plan : undefined;
  const selectedIds = new Set(selected);
  const chosen = selectable.filter(file => selectedIds.has(file.processing_id));

  async function submit() {
    if (submitting.current || !chosen.length || fetching) return;
    submitting.current = true;
    setWorking(true);
    setSummary(null);
    const groups = new Map<string, typeof files>();
    for (const file of chosen) {
      const key = mode === "full" ? file.raw_source_id : file.recording_id!;
      const batch = groups.get(key) ?? [];
      batch.push(file);
      groups.set(key, batch);
    }
    const queue = groups.entries();
    let submitted = 0;
    const errors: string[] = [];
    const failed: string[] = [];
    async function drain() {
      for (const [raw, members] of queue) {
        const first = members[0];
        if (!first) continue;
        try {
          if (mode === "qc") {
            await request({ method: "POST", scope,
              path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/continuous-recordings/${encodeURIComponent(raw)}/quality:retry`,
              body: { request_id: crypto.randomUUID() } });
          } else await request({
            method: "POST", scope, path: `${root}/${encodeURIComponent(raw)}:reprocess-mcap`,
            body: { request_id: crypto.randomUUID(), plan_revision: first.plan!.revision,
              failed_selection: { collection_task_id: first.task.taskId, processing_ids: members.map(f => f.processing_id) } },
          });
          submitted += members.length;
        } catch (error) {
          failed.push(...members.map(f => f.processing_id));
          errors.push(`${members.map(f => f.source_path).join("、")} · ${captureError(error)}`);
        }
      }
    }
    try {
      await Promise.all(Array.from({ length: Math.min(4, groups.size) }, drain));
      setSummary({ submitted, errors });
      setSelected(failed);
      await Promise.all(lists.map(list => list.refetch()));
      for (const key of ["dashboard", "capture-progress", "mcap-preparation", "native-processing", "collection-task-progress",
        "continuous-recordings"]) void client.invalidateQueries({ queryKey: [key] });
    } finally {
      submitting.current = false;
      setWorking(false);
    }
  }

  return <>
    <Button onClick={() => { setSelected([]); setSummary(null); setMode("full"); setOpen(true); }}>批量重试失败项</Button>
    <Modal open={open} width={760} title="批量重试失败项" onCancel={() => { if (!working) setOpen(false); }}
      cancelText="关闭" okText={`重试所选（${chosen.length}）`} onOk={() => void submit()} confirmLoading={working}
      okButtonProps={{ disabled: !chosen.length || fetching }}
      cancelButtonProps={{ disabled: working }}>
      <Space orientation="vertical" style={{ width: "100%" }}>
        <Radio.Group aria-label="重试方式" value={mode} disabled={working} onChange={e => {
          setMode(e.target.value); setSelected([]); setSummary(null);
        }}>
          <Radio value="full">全流程重试</Radio><Radio value="qc">仅重新质检</Radio>
        </Radio.Group>
        {mode === "full" && <p>按{plan ? <>采集方案 <strong>v{plan.scheme.version}</strong></> : "各任务当前采集方案"} 从原始 MCAP 重新执行数据后处理，再进行质检。通过后按任务配置继续入库。</p>}
        {mode === "qc" && <p>使用已生成的录制重新质检。保留现有后处理结果，通过后按任务配置继续入库。</p>}
        {lists.map((list, i) => {
          const task = tasks[i];
          if (!task) return null;
          return list.isError ? <Alert key={task.taskId} type="error"
          title={`${task.name ?? ""} 失败记录读取失败`} description={captureError(list.error)}
          action={<Button onClick={() => void list.refetch()}>刷新</Button>} /> :
          mode === "full" && list.isSuccess && !list.data.plan ? <Alert key={task.taskId} type="warning"
            title={`请先为任务 ${task.name ?? task.taskId} 配置采集方案`} /> : null;
        })}
        {summary && <Alert type={summary.errors.length ? "warning" : "success"} title={`已提交 ${summary.submitted} 条${mode === "full" ? "全流程重试" : "重新质检"}`}
          description={summary.errors.length ? <details><summary>查看未提交原因</summary>{summary.errors.map((e, i) =>
            <div key={i}>{e}</div>)}</details> : undefined} />}
        <Space>
          <Button size="small" disabled={working || fetching || !selectable.length}
            onClick={() => setSelected(selectable.map(f => f.processing_id))}>全选全部失败项（{selectable.length}）</Button>
          <Button size="small" disabled={working || !chosen.length} onClick={() => setSelected([])}>清空选择</Button>
        </Space>
        <Table<(typeof files)[number]> size="small" loading={fetching} rowKey="processing_id" dataSource={files}
          rowSelection={{ selectedRowKeys: chosen.map(f => f.processing_id), preserveSelectedRowKeys: true,
            onChange: keys => { if (!working) setSelected(keys.map(String)); },
            getCheckboxProps: row => ({ disabled: working || (mode === "full" && !row.plan) }) }}
          pagination={{ pageSize: 10, showSizeChanger: false, hideOnSinglePage: true }}
          locale={{ emptyText: mode === "qc" ? "当前范围没有可单独重新质检的录制。成片复检未通过可选全流程重试。" : "当前范围没有可全流程重试的失败 MCAP" }}
          columns={[...(tasks.length > 1 ? [{ title: "采集任务 / 方案", render: (_: unknown, row: (typeof files)[number]) =>
            `${row.task.name ?? row.task.taskId} · ${row.plan ? "v" + row.plan.scheme.version : "待配置"}` }] : []),
            { title: "原始文件", dataIndex: "source_path" },
            { title: "失败环节", dataIndex: "stage", render: (s: FailedFile["stage"]) => ({ QUALITY: "整段质检", EPISODE_QUALITY: "成片质检", PREPARATION: "录制准备", INGEST: "整段入库" })[s] }]} />
      </Space>
    </Modal>
  </>;
}
