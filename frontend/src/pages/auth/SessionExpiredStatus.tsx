import { Clock3 } from "lucide-react";
import { AuthStatusCard } from "./AuthStatusCard";
import styles from "./styles.module.css";

export function SessionExpiredStatus() {
  return (
    <AuthStatusCard
      icon={<Clock3 size={30} />}
      title="登录已过期"
      description={
        <div className={styles.centeredCopy}>
          <p>页面中的认证信息已停止使用。</p>
          <p>
            重新登录后可继续；服务端已接收的上传记录和分片仍会保留，未提交的本地内容可能需要重新选择。
          </p>
        </div>
      }
    />
  );
}
