import { Alert, Button, Drawer, Input, Modal, Space } from "antd";
import { X } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { isDomainError } from "../../../shared/api/domain-error";
import { StatusTag } from "../../../shared/ui";
import type { AccessDecisionInput } from "../access-api";
import type { AccessDecision, AccessRequestRow } from "../contracts";
import {
  availableDecisions,
  decisionLabel,
  statusLabel,
  statusTone,
} from "../presentation";
import styles from "../styles.module.css";
import { AccessRequestDetails } from "./AccessRequestDetails";

export interface ApprovalDrawerProps {
  readonly row: AccessRequestRow;
  readonly open: boolean;
  readonly canManage: boolean;
  readonly principalId: string | null;
  readonly pending: boolean;
  readonly error: unknown;
  readonly settled: boolean;
  readonly onClose: () => void;
  readonly onAfterOpenChange: (open: boolean) => void;
  readonly onReload: () => void;
  readonly onDecision: (input: AccessDecisionInput) => void;
}

function decisionRequiresReason(decision: AccessDecision | null): boolean {
  return decision === "reject" || decision === "revoke";
}

export function ApprovalDrawer({
  canManage,
  error,
  onAfterOpenChange,
  onClose,
  onDecision,
  onReload,
  pending,
  principalId,
  row,
  settled,
  open,
}: Readonly<ApprovalDrawerProps>) {
  const options = availableDecisions(row, canManage, principalId);
  const [decision, setDecision] = useState<AccessDecision | null>(null);
  const [reason, setReason] = useState("");
  const [showValidation, setShowValidation] = useState(false);
  const [confirmMembershipRevoke, setConfirmMembershipRevoke] = useState(false);
  const formId = useId();
  const reasonId = useId();
  const drawerTitleId = useId();
  const revokeTitleId = useId();
  const reasonMissing =
    decisionRequiresReason(decision) && reason.trim().length === 0;
  const domainError = isDomainError(error) ? error : null;

  const emitDecision = () => {
    if (!decision) return;
    onDecision({
      kind: row.kind,
      action: decision,
      projectId: row.projectId,
      requestId: row.requestId,
      reason: reason.trim() || null,
    });
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setShowValidation(true);
    if (!decision || reasonMissing || pending || settled) return;
    if (decision === "revoke" && row.kind === "membership") {
      setConfirmMembershipRevoke(true);
      return;
    }
    emitDecision();
  };

  const title = row.kind === "membership" ? "项目加入申请" : "权限申请";

  return (
    <>
      <Drawer
        rootClassName={styles.approvalDrawerRoot}
        className={styles.approvalDrawer}
        data-e09-drawer
        size={560}
        open={open}
        mask
        keyboard
        aria-labelledby={drawerTitleId}
        closable={false}
        destroyOnHidden
        title={
          <div className={styles.drawerTitle}>
            <h2 id={drawerTitleId}>{title}</h2>
            <StatusTag
              status={row.status}
              label={statusLabel(row.status)}
              tone={statusTone(row.status)}
            />
          </div>
        }
        extra={
          <Button
            type="text"
            icon={<X aria-hidden="true" size={19} />}
            aria-label="关闭申请详情"
            onClick={onClose}
          />
        }
        footer={
          <div className={styles.drawerFooter}>
            <Button disabled={pending} onClick={onClose}>
              {options.length === 0 ? "关闭" : "取消"}
            </Button>
            {decision ? (
              <Button
                form={formId}
                type={decision === "approve" ? "primary" : "default"}
                danger={decision !== "approve"}
                htmlType="submit"
                loading={pending}
                disabled={pending || settled}
              >
                {pending ? "提交中…" : `提交${decisionLabel(decision)}`}
              </Button>
            ) : null}
          </div>
        }
        afterOpenChange={onAfterOpenChange}
        onClose={onClose}
      >
        <form id={formId} className={styles.drawerForm} onSubmit={submit}>
          <AccessRequestDetails row={row} />

          <section
            className={styles.decisionSection}
            aria-labelledby="decision-heading"
          >
            <h3 id="decision-heading">可执行操作</h3>
            {options.length > 0 ? (
              <>
                {row.kind === "membership" && options.includes("approve") ? (
                  <p className={styles.decisionContext}>
                    批准只建立当前项目成员关系，不会自动授予业务能力。
                  </p>
                ) : null}
                <div className={styles.decisionOptions}>
                  {options.map((option) => (
                    <Button
                      key={option}
                      type={option === "approve" ? "primary" : "default"}
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
                {decision ? (
                  <div className={styles.reasonGroup}>
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
                        status={
                          showValidation && reasonMissing ? "error" : undefined
                        }
                        aria-invalid={showValidation && reasonMissing}
                        aria-describedby={
                          showValidation && reasonMissing
                            ? `${reasonId}-error`
                            : undefined
                        }
                        placeholder="填写处理依据或原因…"
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
                        拒绝或撤销必须填写原因。
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className={styles.decisionHint}>
                    选择操作后再填写说明并提交；页面不会自动执行决策。
                  </p>
                )}
                <p className={styles.decisionBoundary}>
                  提交后由服务端再次校验权限与申请状态。
                </p>
              </>
            ) : (
              <p className={styles.readOnlyNote}>
                当前身份或申请状态没有可执行操作，此申请仅供查看。
              </p>
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
                      : domainError?.httpStatus === 403
                        ? "当前权限无法完成操作"
                        : "审批未完成"
                }
                description={
                  <Space orientation="vertical" size="small">
                    <span>
                      {domainError?.message ??
                        "发生未知错误，请重新加载申请后重试。"}
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
        </form>
      </Drawer>

      <Modal
        rootClassName={styles.revokeDialog}
        title={<span id={revokeTitleId}>确认撤销项目成员关系</span>}
        aria-labelledby={revokeTitleId}
        open={confirmMembershipRevoke}
        okText="撤销成员及相关权限"
        cancelText="取消"
        closable={{ "aria-label": "关闭撤销确认" }}
        okButtonProps={{ danger: true }}
        onCancel={() => setConfirmMembershipRevoke(false)}
        onOk={() => {
          setConfirmMembershipRevoke(false);
          emitDecision();
        }}
      >
        <p>
          撤销后将停用该用户在当前项目的成员关系，并同时停用该项目内相关
          capability 授权。这是高影响操作，请确认影响范围。
        </p>
      </Modal>
    </>
  );
}
