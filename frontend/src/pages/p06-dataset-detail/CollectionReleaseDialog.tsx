import {
  Alert,
  Button,
  Checkbox,
  Input,
  Modal,
  Select,
  Space,
  Typography,
} from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { makeScopeKey, type Scope } from "../../shared/scope/model";
import { isDomainError } from "../../shared/api/domain-error";
import { createSessionScopeLease } from "../../shared/api/session-scope-lease";
import {
  preflightCollectionRelease,
  publishAndReadCollectionRelease,
  type CollectionRelease,
  type CollectionReleaseCommand,
  type CollectionReleasePreflight,
} from "../../features/datasets/api/collection-releases";

const reasons = {
  ANNOTATION_NOT_APPROVED: "没有与此数据版本匹配的已审核标注",
  ANNOTATION_TASK_SELECTION_REQUIRED: "有多个已审核标注任务，请明确选择",
  NOT_SELECTED: "本次未选择",
};

export function CollectionReleaseDialog({
  scope,
  datasetId,
  collectionId,
  onClose,
  onPublished,
}: {
  scope: Scope;
  datasetId: string;
  collectionId: string;
  onClose: () => void;
  onPublished: (release: CollectionRelease) => void;
}) {
  const cache = useQueryClient();
  const [name, setName] = useState("");
  const [tasks, setTasks] = useState<Record<string, string>>({});
  const [episodes, setEpisodes] = useState<string[] | null>(null);
  const [options, setOptions] = useState<Record<string, string[]>>({});
  const [preview, setPreview] = useState<CollectionReleasePreflight | null>(
    null,
  );
  const [reviewedCommand, setReviewedCommand] =
    useState<CollectionReleaseCommand | null>(null);
  const [busy, setBusy] = useState<"preflight" | "publish" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      operation.current?.abort();
    },
    [],
  );

  const validName = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/u.test(name.trim());
  const command: CollectionReleaseCommand = {
    dataset_version: name.trim(),
    collection_version_id: collectionId,
    annotation_tasks: tasks,
    episode_ids: episodes,
  };
  const change = () => {
    operation.current?.abort();
    operation.current = null;
    setPreview(null);
    setReviewedCommand(null);
    setError(null);
    setBusy(null);
  };
  const preflight = async () => {
    operation.current?.abort();
    const controller = new AbortController();
    operation.current = controller;
    setBusy("preflight");
    setPreview(null);
    setReviewedCommand(null);
    setError(null);
    const lease = createSessionScopeLease(scope, controller.signal);
    try {
      let submitted = command;
      let result = await preflightCollectionRelease(
        scope,
        datasetId,
        submitted,
        lease.signal,
      );
      // Discovery may include all Episodes; publication always sends the exact list shown here.
      if (submitted.episode_ids === null) {
        submitted = {
          ...submitted,
          episode_ids: Object.keys(result.annotation_task_options).sort(),
        };
        result = await preflightCollectionRelease(
          scope,
          datasetId,
          submitted,
          lease.signal,
        );
      }
      if (controller.signal.aborted) return;
      setOptions(result.annotation_task_options);
      setEpisodes(submitted.episode_ids ?? []);
      setReviewedCommand(submitted);
      setPreview(result);
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(cause instanceof Error ? cause.message : "预检失败，请重试。");
    } finally {
      lease.release();
      if (!controller.signal.aborted) setBusy(null);
    }
  };
  const publish = async () => {
    if (!preview || !reviewedCommand || busy) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy("publish");
    setError(null);
    try {
      const persisted = await publishAndReadCollectionRelease(
        scope,
        datasetId,
        {
          ...reviewedCommand,
          expected_content_hash: preview.content_hash,
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      // Dataset details and delivery entry points all project the new business version.
      await Promise.all(
        [
          "datasets",
          "episode-overview",
          "export-datasets",
          "export-source",
          "export-candidates",
        ].map((domain) =>
          cache.invalidateQueries({ queryKey: [domain, makeScopeKey(scope)] }),
        ),
      );
      if (!controller.signal.aborted) onPublished(persisted);
    } catch (cause) {
      // Keep the exact reviewed request/hash for safe retry after a lost successful response.
      if (
        !controller.signal.aborted &&
        isDomainError(cause) &&
        [401, 403, 409, 422].includes(cause.httpStatus ?? 0)
      ) {
        setPreview(null);
        setReviewedCommand(null);
        if (cause.httpStatus === 409) {
          setOptions({});
          setEpisodes(null);
          setTasks({});
        }
      }
      if (!controller.signal.aborted)
        setError(cause instanceof Error ? cause.message : "发布失败，请重试。");
    } finally {
      if (!controller.signal.aborted) setBusy(null);
    }
  };
  return (
    <Modal
      open
      title="发布已审核的集合版本"
      width={760}
      onCancel={() => {
        if (busy !== "publish") onClose();
      }}
      maskClosable={busy !== "publish"}
      closable={busy !== "publish"}
      footer={
        <Space wrap>
          <Button disabled={busy === "publish"} onClick={onClose}>
            取消
          </Button>
          <Button
            disabled={!validName || busy === "publish"}
            loading={busy === "preflight"}
            onClick={() => void preflight()}
          >
            预检发布内容
          </Button>
          <Button
            type="primary"
            loading={busy === "publish"}
            disabled={!preview?.eligible_episode_ids.length || Boolean(busy)}
            onClick={() => void publish()}
          >
            发布并定版
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph>
        发布固定本次选择的数据版本和已审核标注修订。修改名称或选择后，需要重新预检。
      </Typography.Paragraph>
      <label>
        版本名称
        <Input
          autoFocus
          maxLength={64}
          value={name}
          disabled={busy === "publish"}
          placeholder="例如 v1.0.0"
          onChange={(e) => {
            change();
            setName(e.target.value);
          }}
        />
      </label>
      {name && !validName ? (
        <Typography.Paragraph type="danger">
          名称须以字母或数字开头，仅包含字母、数字、点、下划线或连字符。
        </Typography.Paragraph>
      ) : null}
      <div
        style={{ maxHeight: "40vh", overflowY: "auto", marginBlock: 16 }}
        aria-label="发布 Episode 选择"
      >
        {Object.entries(options).map(([episode, choices]) => (
          <div
            key={episode}
            style={{ paddingBlock: 8, borderBottom: "1px solid #ddd" }}
          >
            <Checkbox
              checked={episodes === null || episodes.includes(episode)}
              disabled={busy === "publish"}
              onChange={(e) => {
                change();
                setEpisodes(
                  e.target.checked
                    ? [
                        ...new Set([
                          ...(episodes ?? Object.keys(options)),
                          episode,
                        ]),
                      ]
                    : (episodes ?? Object.keys(options)).filter(
                        (id) => id !== episode,
                      ),
                );
              }}
            >
              <span style={{ overflowWrap: "anywhere" }}>{episode}</span>
            </Checkbox>
            {choices.length > 1 ? (
              <Select
                aria-label={`标注任务 ${episode}`}
                placeholder="选择已审核标注任务"
                style={{ width: "100%", marginTop: 8 }}
                value={tasks[episode]}
                allowClear
                showSearch
                disabled={busy === "publish"}
                options={choices.map((task) => ({ value: task, label: task }))}
                onChange={(task: string | undefined) => {
                  change();
                  setTasks((current) => {
                    const next = { ...current };
                    if (task) next[episode] = task;
                    else delete next[episode];
                    return next;
                  });
                }}
              />
            ) : (
              <Typography.Paragraph
                type="secondary"
                style={{ overflowWrap: "anywhere", marginBottom: 0 }}
              >
                {choices.length
                  ? `已审核标注任务：${choices[0]}`
                  : reasons.ANNOTATION_NOT_APPROVED}
              </Typography.Paragraph>
            )}
          </div>
        ))}
      </div>
      {preview ? (
        <div aria-label="发布预检结果">
          <Alert
            type={preview.eligible_episode_ids.length ? "success" : "warning"}
            showIcon
            message={`将发布 ${preview.eligible_episode_ids.length} 个 Episode，排除 ${preview.excluded_episodes.length} 个`}
          />
          {preview.eligible_episode_ids.length ? (
            <details>
              <summary>纳入的已审核 Episode</summary>
              <ul>
                {preview.eligible_episode_ids.map((id) => (
                  <li key={id} style={{ overflowWrap: "anywhere" }}>
                    {id}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {preview.excluded_episodes.map((item) => (
            <Typography.Paragraph
              key={item.episode_id}
              style={{ overflowWrap: "anywhere" }}
            >
              {item.episode_id}：{reasons[item.reason]}
            </Typography.Paragraph>
          ))}
          <details>
            <summary>固定版本与校验信息</summary>
            <p style={{ overflowWrap: "anywhere" }}>
              集合：{collectionId}
              <br />
              预检内容：{preview.content_hash}
            </p>
          </details>
        </div>
      ) : null}
      {error ? (
        <Alert
          type="error"
          showIcon
          message="发布操作未完成"
          description={
            <>
              {error}。请求中断时可用相同预检重试；集合或审核变化时请重新预检。
            </>
          }
        />
      ) : null}
    </Modal>
  );
}
