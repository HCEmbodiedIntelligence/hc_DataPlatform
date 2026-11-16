import { useState } from "react";
import { Button, Card, Flex, Segmented, Select, Tag } from "antd";
import { CameraOff, Minus, Play, Plus, Square } from "lucide-react";
import { PageHeader } from "../../shared/ui/layout";
import styles from "./styles.module.css";

const axes = ["X", "Y", "Z"] as const;
type Axis = (typeof axes)[number];
const origin = { X: 0, Y: 0, Z: 0 };
const limit = 100;

export function Component() {
  const [station, setStation] = useState("双臂采集台 A");
  const [active, setActive] = useState(false);
  const [position, setPosition] = useState(origin);
  const [step, setStep] = useState(10);
  const [view, setView] = useState("位姿示意");
  const [camera, setCamera] = useState("主相机");

  function jog(axis: Axis, direction: number) {
    if (!active) return;
    setPosition((point) => ({
      ...point,
      [axis]: Math.min(limit, Math.max(-limit, point[axis] + direction * step)),
    }));
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title="远程遥操作"
        description="在示例工作站预览末端点动和相机视图，所有操作仅在当前页面模拟。"
      />
      <div className={styles.layout}>
        <Card title="工作站视图" extra={<Tag>模拟工作站</Tag>}>
          <Flex gap={12} wrap="wrap" align="center" justify="space-between">
            <Segmented
              aria-label="工作站视图"
              options={["位姿示意", "相机画面"]}
              value={view}
              onChange={setView}
            />
            {view === "相机画面" && (
              <Select
                aria-label="相机视角"
                value={camera}
                onChange={setCamera}
                options={["主相机", "腕部相机"].map((value) => ({ value }))}
              />
            )}
          </Flex>
          <div className={styles.monitor}>
            <span className={styles.caption}>
              {station} · {view === "位姿示意" ? "XY 俯视图" : camera}
            </span>
            {view === "位姿示意" ? (
              <div
                className={styles.plot}
                role="img"
                aria-label={`末端位置 X ${position.X}，Y ${position.Y} 毫米`}
              >
                <span className={styles.horizontal} />
                <span className={styles.vertical} />
                <span className={styles.axisX}>+X</span>
                <span className={styles.axisY}>+Y</span>
                <span
                  className={styles.cursor}
                  style={{
                    left: `${50 + position.X / 2}%`,
                    top: `${50 - position.Y / 2}%`,
                  }}
                />
              </div>
            ) : (
              <div className={styles.offline}>
                <CameraOff size={32} aria-hidden="true" />
                <strong>{camera}尚未接入</strong>
                <span>视频流接入后将在此显示</span>
              </div>
            )}
          </div>
          <dl className={styles.readings} aria-label="模拟末端坐标">
            {axes.map((axis) => (
              <div key={axis}>
                <dt>{axis} 轴</dt>
                <dd>
                  {position[axis]} <small>mm</small>
                </dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card
          title="点动控制"
          extra={
            <Tag color={active ? "processing" : undefined}>
              {active ? "模拟中" : "待机"}
            </Tag>
          }
        >
          <div className={styles.controls}>
            <label htmlFor="teleop-station">示例工作站</label>
            <Select
              id="teleop-station"
              disabled={active}
              value={station}
              options={["双臂采集台 A", "单臂采集台 B"].map((value) => ({
                value,
              }))}
              onChange={(value) => {
                setStation(value);
                setPosition(origin);
              }}
            />
            <label id="teleop-step">点动步长</label>
            <Segmented
              block
              aria-labelledby="teleop-step"
              value={step}
              onChange={setStep}
              options={[1, 5, 10].map((value) => ({
                value,
                label: `${value} mm`,
              }))}
            />
            <div className={styles.jogs}>
              {axes.map((axis) => (
                <div className={styles.jog} key={axis}>
                  <strong>{axis} 轴</strong>
                  <Button
                    aria-label={`${axis} 轴减小`}
                    icon={<Minus size={16} />}
                    disabled={!active || position[axis] <= -limit}
                    onClick={() => jog(axis, -1)}
                  />
                  <Button
                    aria-label={`${axis} 轴增大`}
                    icon={<Plus size={16} />}
                    disabled={!active || position[axis] >= limit}
                    onClick={() => jog(axis, 1)}
                  />
                </div>
              ))}
            </div>
            <span className={styles.hint}>每轴模拟范围 −100 至 100 mm</span>
            <Button
              type="primary"
              danger={active}
              icon={active ? <Square size={15} /> : <Play size={15} />}
              onClick={() => setActive(!active)}
            >
              {active ? "停止模拟" : "开始模拟"}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
