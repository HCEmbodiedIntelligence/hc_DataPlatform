import type { ReactNode } from "react";
import hangchaLogo from "../../assets/hangcha-logo.png";
import styles from "./styles.module.css";

interface AuthStatusCardProps {
  readonly icon: ReactNode;
  readonly title: string;
  readonly description: ReactNode;
  readonly actions?: ReactNode;
  readonly headerAction?: ReactNode;
  readonly compact?: boolean;
  readonly headingLevel?: 1 | 2;
}

export function AuthStatusCard({
  icon,
  title,
  description,
  actions,
  headerAction,
  compact = false,
  headingLevel = 2,
}: AuthStatusCardProps) {
  const Heading = headingLevel === 1 ? "h1" : "h2";
  return (
    <article className={styles.statusCard} data-compact={compact || undefined}>
      <header className={styles.statusHeader}>
        <span className={styles.statusBrand}>
          <img src={hangchaLogo} width="106" height="62" alt="" />
          <span translate="no">HC 数据平台</span>
        </span>
        {headerAction}
      </header>
      <div className={styles.statusBody}>
        <span className={styles.statusIcon} aria-hidden="true">
          {icon}
        </span>
        <Heading>{title}</Heading>
        <div className={styles.statusDescription}>{description}</div>
        {actions ? <div className={styles.statusActions}>{actions}</div> : null}
      </div>
    </article>
  );
}
