import type { ReactNode } from "react";
import hangchaLogo from "../../assets/hangcha-logo.png";
import styles from "./styles.module.css";

interface AuthLayoutProps {
  readonly children?: ReactNode;
  readonly rail: ReactNode;
}

export function AuthLayout({ children, rail }: AuthLayoutProps) {
  return (
    <div className={styles.authPage}>
      <a className={styles.skipLink} href="#auth-content">
        跳到认证内容
      </a>
      <main className={styles.authLayout}>
        <section className={styles.authMain} aria-label="HC 数据平台认证">
          <header className={styles.brandBlock}>
            <img
              className={styles.brandLogo}
              src={hangchaLogo}
              width="106"
              height="62"
              alt="杭叉集团"
              fetchPriority="high"
            />
            <span className={styles.brandName} translate="no">
              HC 数据平台
            </span>
          </header>

          <div className={styles.blueprint} aria-hidden="true">
            <span className={styles.blueprintBase} />
            <span className={styles.blueprintArmOne} />
            <span className={styles.blueprintJointOne} />
            <span className={styles.blueprintArmTwo} />
            <span className={styles.blueprintJointTwo} />
            <span className={styles.blueprintTool} />
            <span className={styles.blueprintPanel} />
          </div>

          {children ? (
            <div className={styles.authContent} id="auth-content" tabIndex={-1}>
              {children}
            </div>
          ) : (
            <div
              className={styles.authContentAnchor}
              id="auth-content"
              tabIndex={-1}
            />
          )}

          <footer className={styles.authFooter}>
            <span>HC 数据平台</span>
            <span aria-hidden="true">·</span>
            <span>工业数据访问入口</span>
          </footer>
        </section>
        <aside className={styles.stateRail} aria-label="账户状态">
          {rail}
        </aside>
      </main>
    </div>
  );
}
