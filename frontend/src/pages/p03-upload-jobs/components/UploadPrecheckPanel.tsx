import { Alert, Button, Spin } from "antd";
import {
  Check,
  CircleAlert,
  FileJson2,
  ListChecks,
  ServerCog,
} from "lucide-react";
import type { UploadFlowState } from "../upload-flow";
import styles from "../styles.module.css";

type PrecheckState = Extract<
  UploadFlowState,
  { readonly phase: "prechecking" | "precheck_failed" }
>;

const stageOrder = [
  "submitting_manifest",
  "validating_manifest",
  "creating_queue",
] as const;

function stageIndex(state: Extract<PrecheckState, { phase: "prechecking" }>) {
  return stageOrder.indexOf(state.stage);
}

export function UploadPrecheckPanel(props: {
  readonly state: PrecheckState;
  readonly onRetry: () => void;
  readonly onBack: () => void;
  readonly onModify: () => void;
}) {
  const running = props.state.phase === "prechecking";
  const currentIndex = running ? stageIndex(props.state) : -1;
  return (
    <section
      className={`${styles.uploadPanel} ${styles.serialPrecheckPanel}`}
      aria-labelledby="upload-precheck-heading"
    >
      <header className={styles.precheckHeading}>
        <span className={styles.stepBadge} aria-hidden="true">
          3
        </span>
        <div>
          <h2 id="upload-precheck-heading">上传前预检</h2>
          <p>
            {running
              ? "当前状态直接来自服务端请求；接口未返回细分百分比，因此不显示模拟进度条。"
              : "服务端没有创建可上传队列，请处理错误后重新检查。"}
          </p>
        </div>
        {running ? <Spin size="small" aria-label="服务端预检进行中" /> : null}
      </header>

      {running ? (
        <ol className={styles.realStageList} aria-label="服务端预检阶段">
          <li data-state={currentIndex > 0 ? "done" : "current"}>
            <FileJson2 size={18} aria-hidden="true" />
            <div>
              <strong>提交 Manifest</strong>
              <span>将用户确认的 Manifest 和目标作用域提交到平台。</span>
            </div>
            {currentIndex > 0 ? <Check size={17} aria-hidden="true" /> : null}
          </li>
          <li
            data-state={
              currentIndex > 1
                ? "done"
                : currentIndex === 1
                  ? "current"
                  : "pending"
            }
          >
            <ServerCog size={18} aria-hidden="true" />
            <div>
              <strong>服务端解析与校验 Manifest</strong>
              <span>
                校验声明、目标信息、文件清单、大小和校验值声明；服务端以同步结果返回，不暴露内部子阶段。
              </span>
              {currentIndex === 1 && props.state.completedUnits > 0 ? (
                <small>
                  已返回 {props.state.completedUnits} /{" "}
                  {props.state.selection.units.length} 个上传单元
                </small>
              ) : null}
            </div>
            {currentIndex > 1 ? <Check size={17} aria-hidden="true" /> : null}
          </li>
          <li data-state={currentIndex === 2 ? "current" : "pending"}>
            <ListChecks size={18} aria-hidden="true" />
            <div>
              <strong>创建上传任务与分片队列</strong>
              <span>
                创建服务端会话并取得初始分片授权；完成后才会显示正式上传队列。
              </span>
              {currentIndex === 2 ? (
                <small>
                  已创建 {props.state.completedUnits} /{" "}
                  {props.state.selection.units.length} 个服务端任务
                </small>
              ) : null}
            </div>
          </li>
        </ol>
      ) : (
        <div className={styles.precheckFailure}>
          <Alert
            type="error"
            showIcon
            icon={<CircleAlert size={18} />}
            title={props.state.problem.title}
            description={
              <div>
                <p>{props.state.problem.detail}</p>
                {props.state.problem.problemCode ? (
                  <code>{props.state.problem.problemCode}</code>
                ) : null}
                {props.state.problem.requestId ? (
                  <p>
                    请求 ID：<code>{props.state.problem.requestId}</code>
                  </p>
                ) : null}
              </div>
            }
          />
          {props.state.preparedItemIds.length > 0 ? (
            <Alert
              type="warning"
              showIcon
              title={`已有 ${props.state.preparedItemIds.length} 个任务完成创建，但传输尚未开始`}
              description="重新检查会复用这些服务端任务；返回重新选择时，平台会先要求确认取消这些任务。"
            />
          ) : null}
          <div className={styles.precheckFailureActions}>
            <Button type="primary" onClick={props.onRetry}>
              重新检查
            </Button>
            <Button onClick={props.onModify}>返回修改</Button>
            <Button type="text" onClick={props.onBack}>
              返回重新选择文件夹
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
