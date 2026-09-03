import { ShieldCheck } from "lucide-react";
import { AuthStatusCard } from "./AuthStatusCard";
import styles from "./styles.module.css";

export function AccessGuideStatus() {
  return (
    <AuthStatusCard
      icon={<ShieldCheck size={30} />}
      title="账户访问从这里开始"
      description={
        <ol className={styles.accessSteps}>
          <li>
            <strong>创建账户</strong>
            <span>只需用户名和密码，无需账户审批。</span>
          </li>
          <li>
            <strong>直接登录</strong>
            <span>新账户登录后先进入空账户状态。</span>
          </li>
          <li>
            <strong>按需申请</strong>
            <span>项目加入和业务权限分别申请、分别审批。</span>
          </li>
        </ol>
      }
    />
  );
}
