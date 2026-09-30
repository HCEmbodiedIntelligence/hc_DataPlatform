import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Typography } from "antd";
import type { Scope } from "../../entities/scope";
import styles from "./styles.module.css";
import {
  loadDataReviews,
  recordDataReview,
  type DataReviewCommand,
  type ReviewDataRef,
} from "../../features/datasets/episodes/data-reviews";

const statusLabel = {
  NOT_REVIEWED: "尚未审核",
  APPROVED: "采集合规通过",
  RETURNED: "需返工",
};

export function DataReviewPanel({
  scope,
  data,
  canRead,
  canReview,
}: {
  scope: Scope;
  data: ReviewDataRef;
  canRead: boolean;
  canReview: boolean;
}) {
  const queryClient = useQueryClient();
  const [before, setBefore] = useState<number | undefined>();
  const [comment, setComment] = useState("");
  const [decision, setDecision] = useState<"APPROVED" | "RETURNED">("RETURNED");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const pending = useRef<{
    draft: string;
    command: DataReviewCommand;
    key: string;
  } | null>(null);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);
  const query = useQuery({
    queryKey: ["episode-data-reviews", scope, data, before],
    queryFn: ({ signal }) => loadDataReviews(scope, data, signal, before),
    enabled: canRead,
    staleTime: 0,
  });
  const mutation = useMutation({
    mutationFn: async () => {
      if (!query.data || !canReview)
        throw new Error("当前数据审核状态或权限不可用。");
      const draft = JSON.stringify({
        comment: comment.trim(),
        decision,
        start,
        end,
      });
      if (!pending.current || pending.current.draft !== draft) {
        pending.current = {
          draft,
          key: crypto.randomUUID(),
          command: {
            expected_content_hash: data.content_hash,
            expected_sequence: query.data.current_sequence,
            decision,
            comment: comment.trim(),
            locations:
              start === "" && end === ""
                ? []
                : [
                    {
                      start_step: Number(start),
                      end_step: Number(end),
                      tag: null,
                      comment: "此区间对应本次审核意见。",
                    },
                  ],
          },
        };
      }
      return recordDataReview(
        scope,
        data,
        pending.current.command,
        pending.current.key,
        lifetime.current?.signal,
      );
    },
    onSuccess: async () => {
      pending.current = null;
      setComment("");
      setStart("");
      setEnd("");
      setBefore(undefined);
      await query.refetch();
      await queryClient.invalidateQueries({
        queryKey: ["episode-overview", scope, data],
      });
    },
  });
  const rangeValid =
    (start === "" && end === "") ||
    (/^(0|[1-9][0-9]*)$/u.test(start) &&
      /^(0|[1-9][0-9]*)$/u.test(end) &&
      Number.isSafeInteger(Number(start)) &&
      Number.isSafeInteger(Number(end)) &&
      Number(start) < Number(end) &&
      Number(end) <= data.frame_count);
  const canSubmit =
    canReview &&
    query.data?.allowed_actions.some(
      (item) => item.action === "SUBMIT_DATA_REVIEW" && item.allowed,
    );
  return (
    <Card title="人工数据审核" size="small">
      <Typography.Paragraph>
        数据版本 {data.version} · {data.frame_count}{" "}
        帧。评估采集是否符合任务要求。
      </Typography.Paragraph>
      {!canRead ? (
        <Alert type="warning" title="缺少数据版本读取权限" />
      ) : (
        <>
          {query.isPending && (
            <Typography.Paragraph>加载审核记录…</Typography.Paragraph>
          )}
          {query.error && (
            <Alert
              type="error"
              title="审核记录加载失败"
              description={query.error.message}
            />
          )}
          {query.data && (
            <>
              <Typography.Paragraph strong>
                人工审核：{statusLabel[query.data.status]}
              </Typography.Paragraph>
              {query.data.items.map((item) => (
                <Card key={item.review_id} size="small">
                  <Typography.Paragraph>
                    第 {item.sequence} 次 · {statusLabel[item.decision]}
                  </Typography.Paragraph>
                  <Typography.Paragraph>
                    {item.actor_id} ·{" "}
                    {new Date(item.created_at).toLocaleString()}
                  </Typography.Paragraph>
                  <Typography.Paragraph>{item.comment}</Typography.Paragraph>
                  {item.locations.map((location, index) => (
                    <Typography.Paragraph key={index}>
                      {location.start_step !== null &&
                        `帧 [${location.start_step}, ${location.end_step}) · `}
                      {location.tag &&
                        `标签 ${location.tag.annotation_id}（修订 ${location.tag.revision}）· `}
                      {location.comment}
                    </Typography.Paragraph>
                  ))}
                </Card>
              ))}
              {query.data.next_before !== null && (
                <Button
                  disabled={mutation.isPending}
                  onClick={() => setBefore(query.data!.next_before!)}
                >
                  更早记录
                </Button>
              )}
            </>
          )}
          <Button
            disabled={mutation.isPending}
            onClick={() => {
              pending.current = null;
              mutation.reset();
              setBefore(undefined);
              void query.refetch();
            }}
          >
            刷新审核状态
          </Button>
          {!canSubmit && query.data && (
            <Alert type="info" title="当前账号没有人工数据审核权限" />
          )}
          {canSubmit && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (rangeValid && comment.trim()) mutation.mutate();
              }}
            >
              <fieldset
                className={styles.reviewForm}
                disabled={mutation.isPending}
              >
                <legend>记录新决定</legend>
                <label>
                  审核决定
                  <select
                    value={decision}
                    onChange={(event) =>
                      setDecision(event.target.value as typeof decision)
                    }
                  >
                    <option value="RETURNED">需返工</option>
                    <option value="APPROVED">采集合规通过</option>
                  </select>
                </label>
                <label>
                  审核意见
                  <textarea
                    value={comment}
                    maxLength={4000}
                    required
                    onChange={(event) => setComment(event.target.value)}
                  />
                </label>
                <details>
                  <summary>定位到帧区间（可选）</summary>
                  <Typography.Paragraph>
                    从 0 开始，采用 [start,end)，结束帧不包含在内。
                  </Typography.Paragraph>
                  <label>
                    起始帧
                    <input
                      inputMode="numeric"
                      value={start}
                      onChange={(event) => setStart(event.target.value)}
                    />
                  </label>
                  <label>
                    结束帧
                    <input
                      inputMode="numeric"
                      value={end}
                      onChange={(event) => setEnd(event.target.value)}
                    />
                  </label>
                </details>
                {!rangeValid && (
                  <Alert
                    type="warning"
                    title="请填写有效区间，结束帧不能超过当前总帧数"
                  />
                )}
                <Button
                  htmlType="submit"
                  disabled={!rangeValid || !comment.trim()}
                  loading={mutation.isPending}
                >
                  提交人工审核
                </Button>
              </fieldset>
            </form>
          )}
          {mutation.error && (
            <Alert
              type="error"
              title="人工审核提交失败"
              description={mutation.error.message}
            />
          )}
          {mutation.isSuccess && (
            <Alert type="success" title="人工审核已记录" />
          )}
        </>
      )}
    </Card>
  );
}
