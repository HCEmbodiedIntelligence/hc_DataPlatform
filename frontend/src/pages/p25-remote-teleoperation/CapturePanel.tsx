import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Flex, Select, Typography } from "antd";
import { request } from "../../shared/api/http-client";
import type { components } from "../../shared/api/generated/platform";

type Capture = components["schemas"]["CaptureView"];
type Command = components["schemas"]["CaptureCommand"];
type TaskPage = components["schemas"]["CollectionTaskPage"];
const labels = {
  idle: "未录制",
  pending: "等待机器人执行",
  unknown: "等待机端核实",
  recording: "录制中",
  saved: "已在机器人保存",
  interrupted: "录制已中断",
  error: "命令未成功",
  busy: "其他会话正在录制",
};

export function CapturePanel({
  org,
  project,
  robot,
  session,
}: {
  org: string;
  project: string;
  robot: string;
  session: string;
}) {
  const [state, setState] = useState<Capture | null>(null);
  const [selected, setSelected] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const busy = useRef(false);
  const path =
    "/organizations/" +
    encodeURIComponent(org) +
    "/teleoperation/robots/" +
    encodeURIComponent(robot) +
    "/capture";
  useEffect(() => {
    const ctl = new AbortController();
    let timer = 0;
    const load = async () => {
      try {
        const next = await request<Capture>({
          path,
          method: "GET",
          scopeMode: "organization",
          scope: { organizationId: org },
          query: { project_id: project },
          cache: "no-store",
          signal: ctl.signal,
        });
        if (!ctl.signal.aborted)
          setState((prev) =>
            !prev || next.revision >= prev.revision ? next : prev,
          );
      } catch (e) {
        if (!ctl.signal.aborted) {
          setState(null);
          setError(e instanceof Error ? e.message : "录制状态查询失败");
        }
      } finally {
        if (!ctl.signal.aborted) timer = window.setTimeout(load, 1500);
      }
    };
    void load();
    return () => {
      ctl.abort();
      clearTimeout(timer);
    };
  }, [org, path, project]);
  const supported = !!state?.supported;
  useEffect(() => {
    if (!supported) return;
    const ctl = new AbortController();
    async function loadNames() {
      const options: Record<string, string> = {};
      let cursor: string | undefined;
      do {
        const page: TaskPage = await request({
          path:
            "/projects/" + encodeURIComponent(project) + "/collection-tasks",
          method: "GET",
          scope: { organizationId: org, projectId: project },
          query: { status: "ACTIVE", limit: 100, cursor },
          signal: ctl.signal,
        });
        for (const task of page.items)
          options[task.collection_task_id] = task.name;
        cursor = page.next_cursor ?? undefined;
      } while (cursor && !ctl.signal.aborted);
      if (!ctl.signal.aborted) setNames(options);
    }
    void loadNames().catch((e) => {
      if (!ctl.signal.aborted) setError(String(e.message ?? e));
    });
    return () => ctl.abort();
  }, [org, project, supported]);
  const allowed = (state?.eligible || []).filter((id) => names[id]);
  const task = state?.active ? state.task_id : selected;
  const blocked =
    !session ||
    !supported ||
    sending ||
    !state ||
    ["pending", "unknown", "busy"].includes(state.state);
  async function issue(action: Command["action"]) {
    if (!state || busy.current || blocked) return;
    busy.current = true;
    setSending(true);
    setError("");
    const body: Command = {
      request_id: crypto.randomUUID(),
      session_id: session,
      project_id: project,
      task_id: task,
      action,
      revision: state.revision,
    };
    try {
      const next = await request<Capture>({
        path,
        method: "POST",
        scopeMode: "organization",
        scope: { organizationId: org },
        body,
        signal: AbortSignal.timeout(8000),
      });
      setState((prev) =>
        !prev || next.revision >= prev.revision ? next : prev,
      );
    } catch (e) {
      setError(
        (e instanceof Error ? e.message : "命令提交失败") +
          "；请核对机器人状态。",
      );
    } finally {
      busy.current = false;
      setSending(false);
    }
  }
  return (
    <Card title="任务录制">
      {error && (
        <Alert
          type="warning"
          message={error}
          closable
          onClose={() => setError("")}
        />
      )}
      <Typography.Paragraph>
        {state ? labels[state.state] : "正在查询录制状态"}
        {state && !supported ? " · 机器人未上报本项目录制能力" : ""}{" "}
        {state?.detail}
      </Typography.Paragraph>
      {state?.record_id && (
        <Typography.Paragraph>
          记录 {state.record_id}{" "}
          {state.upload
            ? (
                {
                  pending: "等待上传",
                  uploaded: "已上传",
                  failed: "上传失败",
                } as Record<string, string>
              )[state.upload]
            : ""}
        </Typography.Paragraph>
      )}
      <Flex wrap gap={8}>
        <Select
          aria-label="采集任务"
          placeholder="选择采集任务"
          style={{ minWidth: 200 }}
          value={task || undefined}
          options={allowed.map((value) => ({ value, label: names[value] }))}
          onChange={setSelected}
          disabled={blocked || state?.active}
        />
        <Button
          disabled={blocked || !!state?.active || !allowed.includes(task)}
          onClick={() => void issue("start")}
        >
          开始录制
        </Button>
        <Button
          disabled={blocked || !state?.active}
          onClick={() => void issue("mark")}
        >
          打标
        </Button>
        <Button
          disabled={blocked || !state?.active}
          onClick={() => void issue("stop")}
        >
          结束并保存
        </Button>
      </Flex>
    </Card>
  );
}
