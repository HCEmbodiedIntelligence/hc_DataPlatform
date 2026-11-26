import { useEffect, useState, type RefObject } from "react";
import { Alert, Button, Card, Flex, Input, Typography } from "antd";
import {
  PairingRequired,
  freshSample,
  readWorkstation,
  ticketKey,
  type LocalState,
} from "./workstation";
import styles from "./styles.module.css";

export function WorkstationCard({
  scope,
  latest,
}: {
  scope: string;
  latest: RefObject<LocalState | null>;
}) {
  const [code, setCode] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState("");
  const [sample, setSample] = useState<LocalState | null>(null);
  const [linking, setLinking] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    setLinking(true);
    latest.current = null;
    setSample(null);
    let lastPaint = 0;
    const poll = window.setInterval(() => {
      if (latest.current && Date.now() - latest.current.sent_ms > 800) {
        latest.current = null;
        setSample(null);
        setError("本机数据流已中断，可重新连接。");
      }
    }, 200);
    let retry = 0;
    const connect = () =>
      void readWorkstation(scope, controller.signal, (next) => {
        latest.current = next;
        if (performance.now() - lastPaint > 100 || !lastPaint) {
          lastPaint = performance.now();
          setSample(next);
          setError("");
          setLinking(false);
        }
      }).catch((cause) => {
        if (!controller.signal.aborted) {
          latest.current = null;
          setSample(null);
          setLinking(false);
          if (!(cause instanceof PairingRequired))
            retry = window.setTimeout(connect, 2000);
          setError(
            cause instanceof TypeError
              ? "未能连接客户端。请启动 EXE，并允许此站点访问本机网络。"
              : String(cause.message ?? cause),
          );
        }
      });
    connect();
    return () => {
      window.clearTimeout(retry);
      controller.abort();
      window.clearInterval(poll);
      latest.current = null;
    };
  }, [scope, attempt, latest]);

  const frame = freshSample(sample);
  const hand = (side: "left" | "right") =>
    !frame?.tracking[side]
      ? "等待有效位姿"
      : frame.protocol_version < 2
        ? "已跟踪（仅位姿）"
        : "已跟踪　扳机 " +
          frame.inputs[side].trigger.toFixed(2) +
          "　握把 " +
          frame.inputs[side].grip.toFixed(2);
  return (
    <Card title="本机 Windows 设备">
      {sample ? (
        <dl className={styles.details}>
          <dt>工作站</dt>
          <dd>{sample.workstation}</dd>
          <dt>PICO</dt>
          <dd>{sample.error || sample.peer || "等待头显应用发送输入"}</dd>
          <dt>头部</dt>
          <dd>{frame?.tracking.head ? "已跟踪" : "等待有效位姿"}</dd>
          <dt>左手柄</dt>
          <dd>{hand("left")}</dd>
          <dt>右手柄</dt>
          <dd>{hand("right")}</dd>
        </dl>
      ) : (
        <Typography.Paragraph>
          {error || (linking ? "正在连接本机…" : "等待本机客户端")}
        </Typography.Paragraph>
      )}
      {!sample && (
        <Flex gap={8} wrap>
          <Input.Password
            aria-label="本机配对码"
            placeholder="从 EXE 复制配对码"
            value={code}
            autoComplete="off"
            maxLength={32}
            onChange={(e) => setCode(e.target.value.trim())}
            style={{ maxWidth: 270 }}
          />
          <Button
            onClick={() => {
              if (code && !/^[A-Za-z0-9_-]{32}$/.test(code)) {
                setError("请粘贴客户端复制的完整配对码");
                return;
              }
              if (code) sessionStorage.setItem(ticketKey, code);
              setCode("");
              setAttempt((i) => i + 1);
            }}
          >
            {code ? "配对本机" : "重新连接"}
          </Button>
        </Flex>
      )}
      {sample?.error && <Alert type="warning" message={sample.error} />}
    </Card>
  );
}
