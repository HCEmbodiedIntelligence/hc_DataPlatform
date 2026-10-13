import { Check } from "lucide-react";
import { AuthStatusCard } from "./AuthStatusCard";
import styles from "./styles.module.css";

interface RegistrationSuccessStatusProps {
  readonly compact?: boolean;
}

export function RegistrationSuccessStatus({
  compact = false,
}: RegistrationSuccessStatusProps) {
  return (
    <AuthStatusCard
      compact={compact}
      icon={<Check size={30} strokeWidth={2.2} />}
      title="账户创建成功"
      description={
        <div className={styles.centeredCopy}>
          <p>现在可以直接登录。</p>
          <p>若你是平台首个注册用户，登录后即可使用管理员权限；其他账户需申请加入项目和业务权限。</p>
        </div>
      }
    />
  );
}
