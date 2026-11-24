import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Flex, Spin, Tag, Typography } from "antd";
import { RefreshCw } from "lucide-react";
import {
  useScopedQuery,
  useScopeQueryRevision,
} from "../../shared/api/use-scoped-query";
import { useOrganizationCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { PageHeader } from "../../shared/ui/layout";
import { connectVR, onlineRobots, type OnlineRobot } from "./connection";
import type { DeviceStatus } from "./vr-view";
import styles from "./styles.module.css";

const idle: DeviceStatus = { video: false, control: false, inputs: [] };

function Workspace({ org, allowed }: { org: string; allowed: (id: string) => boolean }) {
  const [vr, setVr] = useState("正在检测");
  const [supported, setSupported] = useState(false);
  const [robot, setRobot] = useState<OnlineRobot | null>(null);
  const [phase, setPhase] = useState("未连接");
  const [device, setDevice] = useState(idle);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  const robots = useScopedQuery(
    org ? { organizationId: org } : null,
    "teleoperation",
    "robots",
    null,
    {
      queryFn: ({ signal }) => onlineRobots(org, signal),
      refetchInterval: 3000,
      retry: false,
    },
  );
  const detect = useCallback(async () => {
    let available = false;
    let message = "本机未检测到可用头显";
    if (!window.isSecureContext)
      message = "请通过 HTTPS 或 localhost 访问以检测头显";
    else if (!navigator.xr) message = "当前浏览器未提供 WebXR";
    else {
      try {
        available = await navigator.xr.isSessionSupported("immersive-vr");
        if (available) message = "本机头显可用";
      } catch {
        message = "无法访问 VR 设备，请检查浏览器权限";
      }
    }
    if (mounted.current) {
      setSupported(available);
      setVr(message);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void detect();
    navigator.xr?.addEventListener("devicechange", detect);
    const leave = () => active.current?.abort();
    window.addEventListener("pagehide", leave);
    return () => {
      mounted.current = false;
      navigator.xr?.removeEventListener("devicechange", detect);
      window.removeEventListener("pagehide", leave);
      leave();
    };
  }, [detect]);

  async function connect(target: OnlineRobot) {
    if (active.current || !allowed(target.robot_id)) return;
    const life = new AbortController();
    active.current = life;
    setRobot(target);
    setError("");
    setDevice(idle);
    setPhase("等待头显授权");
    try {
      await connectVR(org, target.robot_id, life.signal, setPhase, setDevice);
    } catch (cause) {
      if (mounted.current && !life.signal.aborted)
        setError(cause instanceof Error ? cause.message : "连接失败");
    } finally {
      active.current = null;
      if (mounted.current) {
        setRobot(null);
        setDevice(idle);
        setPhase("未连接");
        void robots.refetch();
        void detect();
      }
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title="远程遥操作"
        description="选择在线机器人，连接这台电脑上的 VR 设备。"
      />
      {error && <Alert type="error" showIcon message={error} />}
      <div className={styles.layout}>
        <Card
          title="支持遥操作的机器人"
          extra={
            <Button
              icon={<RefreshCw size={15} />}
              aria-label="刷新在线机器人"
              onClick={() => void robots.refetch()}
              disabled={!org}
            />
          }
        >
          {!org ? (
            <Empty description="请先选择组织" />
          ) : robots.isError ? (
            <Alert
              type="error"
              showIcon
              message="无法获取在线机器人"
              description={robots.error.message}
            />
          ) : robots.isPending ? (
            <Spin aria-label="正在获取机器人" />
          ) : robots.data.length === 0 ? (
            <Empty description="当前没有机器人上报遥操作心跳" />
          ) : (
            <ul className={styles.robots}>
              {robots.data.map((item) => (
                <li key={item.robot_id}>
                  <div>
                    <strong>{item.name}</strong>
                    <Typography.Text type="secondary">
                      {item.robot_id}
                    </Typography.Text>
                  </div>
                  <Tag color={item.busy ? "gold" : "green"}>
                    {item.busy ? "占用中" : "可连接"}
                  </Tag>
                  <Button
                    disabled={!!robot || item.busy || !supported || !allowed(item.robot_id)}
                    onClick={() => void connect(item)}
                  >
                    连接
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {robots.data && robots.data.every(item => !allowed(item.robot_id)) && (
            <Typography.Text type="secondary">
              当前账号没有遥操作连接权限。
            </Typography.Text>
          )}
        </Card>
        <Card
          title="本机 VR 与连接状态"
          extra={
            <Button onClick={() => void detect()} disabled={!!robot}>
              检测设备
            </Button>
          }
        >
          <dl className={styles.details}>
            <dt>VR 头显</dt>
            <dd>{robot && phase !== "等待头显授权" ? "已进入 VR 会话" : vr}</dd>
            <dt>输入设备</dt>
            <dd>
              {device.inputs.join("、") ||
                (robot ? "等待设备输入" : "进入 VR 后检测")}
            </dd>
            <dt>目标机器人</dt>
            <dd>{robot?.name || "尚未选择"}</dd>
            <dt>网络连接</dt>
            <dd>{phase}</dd>
            <dt>机器人视频</dt>
            <dd>
              {device.video ? "正在接收画面" : robot ? "等待视频帧" : "未连接"}
            </dd>
            <dt>控制通道</dt>
            <dd>
              {device.control ? "已打开" : robot ? "等待通道打开" : "未连接"}
            </dd>
          </dl>
          {robot && (
            <Flex justify="end">
              <Button onClick={() => active.current?.abort()}>断开连接</Button>
            </Flex>
          )}
        </Card>
      </div>
    </div>
  );
}

export function Component() {
  const org = useShellStore((s) => s.scope?.organizationId ?? "");
  const revision = useScopeQueryRevision();
  const { has } = useOrganizationCapabilities(org);
  const allowed = (id: string) => has("teleoperation.connect") || has(`teleoperation.connect/${id}`);
  return (
    <Workspace
      key={`${org}/${revision}`}
      org={org}
      allowed={allowed}
    />
  );
}
