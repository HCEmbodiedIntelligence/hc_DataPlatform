import { Alert, Button, Input, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import type { IngestScope } from "../../entities/data-source";
import { request } from "../../shared/api/http-client";
import { getRuntimeConfig } from "../../shared/config/runtime";
import { copyText } from "../../shared/lib/copy-text";
import { robotConnectionBaseUrl } from "./connection-url";

const receipt = z.object({
  ticket: z.string(),
  expires_at: z.string(),
  expires_in_seconds: z.number(),
});

export function ConnectionLink({
  scope,
  identityId,
  disabled = false,
}: {
  scope: IngestScope;
  identityId: string;
  disabled?: boolean;
}) {
  const [value, setValue] = useState<{ link: string; expires: number } | null>(
    null,
  );
  const [seconds, setSeconds] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    setValue(null);
    setError("");
    setCopied(false);
    generation.current++;
    return () => {
      generation.current++;
    };
  }, [scope.organizationId, identityId]);
  useEffect(() => {
    const tick = () =>
      setSeconds(
        value ? Math.max(0, Math.ceil((value.expires - Date.now()) / 1000)) : 0,
      );
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [value]);
  const copy = async () => {
    const currentGeneration = generation.current;
    setBusy(true);
    setError("");
    setCopied(false);
    try {
      let current = value;
      if (!current || current.expires <= Date.now()) {
        const base = robotConnectionBaseUrl(
          getRuntimeConfig(),
          window.location.origin,
        );
        const result = receipt.parse(
          await request({
            method: "POST",
            scope,
            scopeMode: "explicit",
            path: `/projects/${encodeURIComponent(scope.projectId)}/robot-ingest/identities/${encodeURIComponent(identityId)}/connections`,
            body: {},
            cache: "no-store",
          }),
        );
        if (generation.current !== currentGeneration) return;
        base.hash = `robot-connect=${encodeURIComponent(result.ticket)}`;
        current = {
          link: base.toString(),
          expires: Date.parse(result.expires_at),
        };
        setValue(current);
      }
      if (await copyText(current.link)) {
        setCopied(true);
      } else {
        setError("浏览器无法复制，请选中下方连接链接手动复制。");
      }
    } catch (cause) {
      if (generation.current === currentGeneration)
        setError(
          cause instanceof Error ? cause.message : "连接链接生成失败，请重试",
        );
    } finally {
      if (generation.current === currentGeneration) setBusy(false);
    }
  };
  return (
    <Space direction="vertical" style={{ width: "100%", marginBlock: 16 }}>
      <Typography.Text strong>连接采集端</Typography.Text>
      <Typography.Paragraph>
        在机器人页面粘贴连接链接并点击“连接”，然后按名称选择采集任务。链接有效期为
        10 分钟。
      </Typography.Paragraph>
      <Button
        type="primary"
        disabled={disabled}
        loading={busy}
        onClick={() => void copy()}
      >
        {copied ? "已复制连接链接" : "复制连接链接"}
      </Button>
      {value && (
        <>
          <Input.TextArea
            aria-label="连接链接"
            readOnly
            autoComplete="off"
            data-sensitive="credential"
            value={value.link}
            rows={2}
          />
          <Typography.Text type={seconds > 0 ? "secondary" : "warning"}>
            {seconds > 0
              ? `剩余 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒 · 成功连接后链接失效`
              : "链接已过期，请重新生成"}
          </Typography.Text>
          <Button
            onClick={() => {
              setValue(null);
              setCopied(false);
            }}
          >
            重新生成链接
          </Button>
        </>
      )}
      {disabled && <Alert type="warning" title="请先恢复机器人上传权限" />}
      {error && <Alert type="warning" title={error} />}
    </Space>
  );
}
