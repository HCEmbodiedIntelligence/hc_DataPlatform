import { PageHeader } from "../../shared/ui/layout/PageHeader";
import { PageState } from "../../shared/ui/state/PageState";
import styles from "./PlatformShell.module.css";

export interface ShellLoadingPageProps {
  title?: string;
  label?: string;
}

export function ShellLoadingPage({
  title = "工作台",
  label = "工作台",
}: Readonly<ShellLoadingPageProps>) {
  return (
    <section
      className={styles.shellLoadingPage}
      aria-labelledby="shell-loading-page-title"
    >
      <PageHeader title={title} headingId="shell-loading-page-title" />
      <PageState state="loading" label={label} layout="dashboard" />
    </section>
  );
}
