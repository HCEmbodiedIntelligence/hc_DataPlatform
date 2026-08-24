import { Button } from "antd";
import {
  Bell,
  FileClock,
  FolderPlus,
  LogOut,
  Settings,
  ShieldPlus,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { AuthStatusCard } from "./AuthStatusCard";
import styles from "./styles.module.css";

export type EmptyAccountIntent = "membership" | "capability" | "history";

interface EmptyAccountStatusProps {
  readonly username?: string;
  readonly compact?: boolean;
  readonly headingLevel?: 1 | 2;
  readonly loggingOut?: boolean;
  readonly notice?: ReactNode;
  readonly onIntent: (intent: EmptyAccountIntent) => void;
  readonly onNotifications?: () => void;
  readonly onLogout?: () => void;
}

export function EmptyAccountStatus({
  username,
  compact = false,
  headingLevel = 2,
  loggingOut = false,
  notice,
  onIntent,
  onNotifications,
  onLogout,
}: EmptyAccountStatusProps) {
  return (
    <AuthStatusCard
      compact={compact}
      headingLevel={headingLevel}
      icon={<FolderPlus size={32} />}
      title="尚未加入项目"
      headerAction={
        onLogout ? (
          <Button
            type="text"
            icon={<LogOut size={17} aria-hidden="true" />}
            loading={loggingOut}
            disabled={loggingOut}
            onClick={onLogout}
          >
            退出登录
          </Button>
        ) : null
      }
      description={
        <div className={styles.centeredCopy}>
          <p>
            {username ? <span translate="no">{username}</span> : "此账户"}{" "}
            已创建并可登录。
          </p>
          <p>加入项目后，才能按已批准的权限协作和管理数据。</p>
        </div>
      }
      actions={
        <>
          {notice}
          <Button
            type="primary"
            block
            icon={<FolderPlus size={17} aria-hidden="true" />}
            onClick={() => onIntent("membership")}
          >
            申请加入项目
          </Button>
          <Button
            block
            icon={<ShieldPlus size={17} aria-hidden="true" />}
            onClick={() => onIntent("capability")}
          >
            申请权限
          </Button>
          <Button
            type="link"
            icon={<FileClock size={17} aria-hidden="true" />}
            onClick={() => onIntent("history")}
          >
            申请记录
          </Button>
          {onNotifications ? (
            <Button
              type="link"
              icon={<Bell size={17} aria-hidden="true" />}
              onClick={onNotifications}
            >
              通知
            </Button>
          ) : null}
          <Link className={styles.accountSettingsLink} to="/account/settings">
            <Settings aria-hidden="true" size={17} />
            账户设置
          </Link>
        </>
      }
    />
  );
}
