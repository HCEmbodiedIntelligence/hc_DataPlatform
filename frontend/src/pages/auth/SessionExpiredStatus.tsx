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
          <p>重新登录后再继续；未提交内容可能无法恢复。</p>
        </div>
      }
    />
  );
}
