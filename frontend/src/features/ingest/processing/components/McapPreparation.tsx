import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Input, InputNumber, Space, Checkbox } from "antd";
import { Link } from "react-router-dom";
import type { IngestScope } from "../../../../entities/data-source";
import { request } from "../../../../shared/api/http-client";
import { nativeRoot } from "../lerobot-processing";

type Profile = {
  state_topic: string | null;
  action_topic: string | null;
  primary_camera: string | null;
  joint_names: string[];
  joint_units: string[];
  depth_scale_m_per_unit: number | null;
  derive_action_from_next_state: boolean;
  maximum_time_error_ms: number;
  fps: number;
};
export function McapRobotProfile({
  scope,
  robotId,
}: {
  scope: IngestScope;
  robotId: string;
}) {
  const path = `${nativeRoot(scope)}/robots/${encodeURIComponent(robotId)}/mcap-profile`;
  const query = useQuery({
    queryKey: ["mcap-profile", scope, robotId],
    enabled: Boolean(robotId),
    queryFn: ({ signal }) =>
      request<Profile>({ method: "GET", scope, path, signal }),
  });
  const [draft, setDraft] = useState<Profile | null>(null);
  useEffect(() => {
    setDraft(query.data ?? null);
  }, [query.data, robotId]);
  const save = useMutation({
    mutationFn: () =>
      request<Profile>({ method: "PUT", path, scope, body: draft }),
    onSuccess: () => query.refetch(),
  });
  if (!robotId) return null;
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <strong>机器人 MCAP 解析配置</strong>
      <span>
        配置随机器人复用。通道唯一时可自动识别；多个候选时填写准确
        topic。保存配置后开始处理。
      </span>
      {draft && (
        <>
          {(
            [
              ["state_topic", "关节状态 topic"],
              ["action_topic", "动作指令 topic"],
              ["primary_camera", "主 RGB 相机 topic"],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              {label}
              <Input
                aria-label={label}
                value={draft[key] ?? ""}
                onChange={(e) =>
                  setDraft({ ...draft, [key]: e.target.value || null })
                }
              />
            </label>
          ))}
          <label>
            关节名称（逗号分隔，留空保留源关节）
            <Input
              value={draft.joint_names.join(",")}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  joint_names: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
            />
          </label>
          <label>
            对应单位（逗号分隔，留空记为 unknown）
            <Input
              value={draft.joint_units.join(",")}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  joint_units: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
            />
          </label>
          <label>
            深度单位换算为米（如毫米为 0.001）
            <InputNumber
              min={0.000001}
              max={1}
              value={draft.depth_scale_m_per_unit}
              onChange={(value) =>
                setDraft({ ...draft, depth_scale_m_per_unit: value })
              }
            />
          </label>
          <Checkbox
            checked={draft.derive_action_from_next_state}
            onChange={(e) =>
              setDraft({
                ...draft,
                derive_action_from_next_state: e.target.checked,
              })
            }
          >
            缺少动作通道时允许使用下一帧状态作为动作
          </Checkbox>
          <Button onClick={() => save.mutate()} loading={save.isPending}>
            保存机器人解析配置
          </Button>
        </>
      )}
      {save.isSuccess && <span>配置已保存</span>}
      {(save.error || query.error) && (
        <Alert
          type="error"
          title="解析配置操作失败"
          description={(save.error || query.error)?.message}
        />
      )}
    </Space>
  );
}

type Progress = {
  files: {
    processing_id: string;
    path: string;
    status: string;
    error: string | null;
    recording_id: string | null;
  }[];
};
export function McapPreparationProgress({
  scope,
  importId,
}: {
  scope: IngestScope;
  importId: string;
}) {
  const query = useQuery({
    queryKey: ["mcap-preparation", scope, importId],
    queryFn: ({ signal }) =>
      request<Progress>({
        method: "GET",
        scope,
        signal,
        path: `${nativeRoot(scope)}/${encodeURIComponent(importId)}/mcap-processing`,
      }),
    refetchInterval: 3000,
  });
  const labels: Record<string, string> = {
    PENDING: "等待处理",
    RUNNING: "准备录制",
    READY: "可预览与切片",
    FAILED: "处理失败",
  };
  return (
    <Space orientation="vertical">
      {query.data?.files.map((f) => (
        <div key={f.processing_id}>
          {f.path} · {labels[f.status] ?? f.status}
          {f.recording_id && (
            <>
              {" "}
              ·{" "}
              <Link
                to={`/recordings/${encodeURIComponent(f.recording_id)}/slice`}
              >
                预览与切片
              </Link>
            </>
          )}
          {f.error && <p role="alert">{f.error}</p>}
        </div>
      ))}
      {query.error && (
        <Alert
          type="error"
          title="MCAP 进度读取失败"
          description={query.error.message}
        />
      )}
    </Space>
  );
}
