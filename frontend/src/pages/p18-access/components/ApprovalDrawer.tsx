import { Alert, Button, Descriptions, Input, Space, Typography } from "antd";
import { KeyRound, ShieldAlert, UserRoundPlus, X } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { isDomainError } from "../../../shared/api/domain-error";
import type { AccessDecisionInput } from "../access-api";
import type { AccessDecision, AccessRequestRow } from "../contracts";
import {
  availableDecisions,
  capabilityLabel,
  decisionLabel,
  elevatedImpactNotes,
  formatDateTime,
  shortIdentity,
} from "../presentation";
import styles from "../styles.module.css";

export interface ApprovalDrawerProps {
  readonly row: AccessRequestRow;
  readonly canManage: boolean;
  readonly principalId: string | null;
  readonly pending: boolean;
  readonly error: unknown;
  readonly settled: boolean;
  readonly onClose: () => void;
  readonly onReload: () => void;
  readonly onDecision: (input: AccessDecisionInput) => void;
}

function decisionRequiresReason(decision: AccessDecision | null): boolean {
  return (
    decision === "reject" || decision === "revoke" || decision === "withdraw"
  );
}

export function ApprovalDrawer({
  canManage,
  error,
  onClose,
  onDecision,
  onReload,
  pending,
  principalId,
  row,
  settled,
}: Readonly<ApprovalDrawerProps>) {
  const options = availableDecisions(row, canManage, principalId);
  const [decision, setDecision] = useState<AccessDecision | null>(
    options[0] ?? null,
  );
  const [reason, setReason] = useState("");
  const [showValidation, setShowValidation] = useState(false);
  const reasonId = useId();
  const impacts = elevatedImpactNotes(row);
  const reasonMissing =
    decisionRequiresReason(decision) && reason.trim().length === 0;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setShowValidation(true);
    if (!decision || reasonMissing) return;
    onDecision({
      kind: row.kind,
      action: decision,
      projectId: row.projectId,
      requestId: row.requestId,
      reason: reason.trim() || null,
    });
  };

  const domainError = isDomainError(error) ? error : null;

  return (
    <div
      className={styles.approvalDrawer}
      data-e09-drawer
      role="dialog"
      aria-modal="false"
      aria-labelledby="p18-approval-title"
    >
      <header className={styles.drawerHeader}>
        <div>
          <span className={styles.drawerEyebrow}>当前项目作用域</span>
          <h2 id="p18-approval-title">审批申请</h2>
        </div>
        <Button
          type="text"
          icon={<X aria-hidden="true" size={19} />}
          aria-label="关闭审批抽屉"
          onClick={onClose}
        />
      </header>

      <form className={styles.drawerForm} onSubmit={submit}>
        <section className={styles.approvalStep}>
          <header>
            <span>1</span>
            <h3>{row.kind === "membership" ? "请求加入项目" : "权限申请人"}</h3>
          </header>
          <Descriptions column={1} size="small" colon={false}>
            <Descriptions.Item label="申请人">
              <code title={row.requesterId} translate="no">
                {shortIdentity(row.requesterId)}
              </code>
            </Descriptions.Item>
            <Descriptions.Item label="项目">
              <code title={row.projectId} translate="no">
                {row.projectId}
              </code>
            </Descriptions.Item>
            <Descriptions.Item label="申请类型">
              <Space size="small">
                {row.kind === "membership" ? (
                  <UserRoundPlus aria-hidden="true" size={15} />
                ) : (
                  <KeyRound aria-hidden="true" size={15} />
                )}
                {row.kind === "membership" ? "项目加入申请" : "权限申请"}
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="申请说明">
              {row.reason || "申请人未填写说明"}
            </Descriptions.Item>
            <Descriptions.Item label="提交时间">
              <time dateTime={row.createdAt}>
                {formatDateTime(row.createdAt)}
              </time>
            </Descriptions.Item>
          </Descriptions>
        </section>

        <section className={styles.approvalStep}>
          <header>
            <span>2</span>
            <h3>请求权限与数据范围</h3>
          </header>
          <Descriptions column={1} size="small" colon={false}>
            <Descriptions.Item label="权限模板">
              {row.kind === "membership"
                ? "正式合同未提供默认角色或模板字段"
                : "正式合同按 capability keys 直接申请，无模板字段"}
            </Descriptions.Item>
            <Descriptions.Item label="请求权限">
              {row.kind === "membership" ? (
                "仅建立当前项目成员关系"
              ) : (
                <ul className={styles.drawerCapabilityList}>
                  {row.capabilityKeys.map((key) => (
                    <li key={key}>
                      <strong>{capabilityLabel(key)}</strong>
                      <code translate="no">{key}</code>
                    </li>
                  ))}
                </ul>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="数据范围">
              当前项目 <code translate="no">{row.projectId}</code>
              ；申请合同未提供 region 或资源谓词
            </Descriptions.Item>
            <Descriptions.Item label="有效期">
              长期有效，直到显式撤销；正式合同无到期时间字段
            </Descriptions.Item>
          </Descriptions>
        </section>

        <section className={styles.approvalStep}>
          <header>
            <span>3</span>
            <h3>风险上下文</h3>
          </header>
          <Descriptions column={1} size="small" colon={false}>
            <Descriptions.Item label="服务端风险等级">
              正式 runtime 合同未返回风险字段
            </Descriptions.Item>
            <Descriptions.Item label="申请修订">
              v{row.revision}
            </Descriptions.Item>
          </Descriptions>
          {impacts.length > 0 ? (
            <Alert
              type="warning"
              showIcon
              title="请求包含高影响能力"
              description={
                <ul className={styles.impactList}>
                  {impacts.map((impact) => (
                    <li key={impact}>{impact}</li>
                  ))}
                </ul>
              }
            />
          ) : (
            <Alert
              type="info"
              showIcon
              title="未从 capability keys 识别到已知高影响动作；这不是服务端风险评级。"
            />
          )}
        </section>

        {impacts.length > 0 ? (
          <section className={styles.approvalStep}>
            <header>
              <span>4</span>
              <h3>高风险再认证</h3>
            </header>
            <Alert
              type="warning"
              showIcon
              icon={<ShieldAlert aria-hidden="true" size={18} />}
              title="再认证合同尚未开放"
              description="runtime OpenAPI 未定义 step-up/reauth 请求或输入字段，本页不会采集密码或动态码。若服务端拒绝操作，将保留真实 401/403 与请求 ID。"
            />
          </section>
        ) : null}

        <section
          className={styles.decisionSection}
          aria-labelledby="decision-heading"
        >
          <h3 id="decision-heading">审批决策</h3>
          {options.length > 0 ? (
            <>
              <div className={styles.decisionOptions}>
                {options.map((option) => (
                  <Button
                    key={option}
                    type={
                      decision === option && option === "approve"
                        ? "primary"
                        : "default"
                    }
                    danger={option !== "approve"}
                    aria-pressed={decision === option}
                    disabled={pending || settled}
                    onClick={() => {
                      setDecision(option);
                      setShowValidation(false);
                    }}
                  >
                    {decisionLabel(option)}
                  </Button>
                ))}
              </div>
              <label className={styles.reasonField} htmlFor={reasonId}>
                <span>
                  {decisionRequiresReason(decision)
                    ? "处理原因（必填）"
                    : "审批说明（选填）"}
                </span>
                <Input.TextArea
                  id={reasonId}
                  name="access-decision-reason"
                  autoComplete="off"
                  value={reason}
                  maxLength={2_000}
                  showCount
                  rows={3}
                  status={showValidation && reasonMissing ? "error" : undefined}
                  aria-invalid={showValidation && reasonMissing}
                  aria-describedby={
                    showValidation && reasonMissing
                      ? `${reasonId}-error`
                      : undefined
                  }
                  placeholder="说明批准依据，或填写拒绝/撤销原因…"
                  disabled={pending || settled}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              {showValidation && reasonMissing ? (
                <p
                  id={`${reasonId}-error`}
                  className={styles.fieldError}
                  role="alert"
                >
                  拒绝、撤销或撤回必须填写原因。
                </p>
              ) : null}
            </>
          ) : (
            <Alert
              type="info"
              showIcon
              title="当前申请为只读"
              description="当前 scope/capability 或申请状态没有允许的动作。服务端仍是最终授权边界。"
            />
          )}
        </section>

        {error ? (
          <div role="alert" className={styles.mutationMessage}>
            <Alert
              type="error"
              showIcon
              title={
                domainError?.httpStatus === 409
                  ? "申请状态已变化"
                  : domainError?.httpStatus === 429
                    ? "审批请求频率受限"
                    : "审批未完成"
              }
              description={
                <Space orientation="vertical" size="small">
                  <span>
                    {domainError?.message ?? "发生未知错误，请重新加载申请。"}
                  </span>
                  {domainError?.problemCode ? (
                    <code translate="no">{domainError.problemCode}</code>
                  ) : null}
                  {domainError?.requestId ? (
                    <span>
                      请求 ID：
                      <code translate="no">{domainError.requestId}</code>
                    </span>
                  ) : null}
                  <Button size="small" onClick={onReload}>
                    重新加载申请
                  </Button>
                </Space>
              }
            />
          </div>
        ) : null}
        <footer className={styles.drawerFooter}>
          <Button disabled={pending} onClick={onClose}>
            取消
          </Button>
          {decision ? (
            <Button
              type={decision === "approve" ? "primary" : "default"}
              danger={decision !== "approve"}
              htmlType="submit"
              loading={pending}
              disabled={options.length === 0 || settled}
            >
              {pending ? "提交中…" : `提交${decisionLabel(decision)}`}
            </Button>
          ) : null}
        </footer>
      </form>
    </div>
  );
}
