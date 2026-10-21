import { Tabs } from "antd";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "../../shared/ui";
import ExternalDataSourcesPage from "./external-page";
import styles from "./styles.module.css";
import { RobotOnboardingTab } from "./components/RobotOnboardingTab";

export default function DataSourcesPage() {
  const [params, setParams] = useSearchParams();
  // Legacy connector deep links still open their preserved project-scoped detail.
  const tab =
    params.get("tab") ?? (params.has("sourceId") ? "external" : "robots");
  return (
    <main className={styles.page} data-page-id="P02">
      <PageHeader
        title="数据源"
        breadcrumbs={[
          { key: "ingest", label: "数据接入", to: "/ingest/uploads/records" },
          { key: "sources", label: "数据源" },
        ]}
      />
      <Tabs
        activeKey={tab}
        destroyOnHidden
        onChange={(tab) => {
          const next = new URLSearchParams(params);
          next.set("tab", tab);
          setParams(next);
        }}
        items={[
          {
            key: "robots",
            label: "机器人接入",
            children: <RobotOnboardingTab />,
          },
          {
            key: "external",
            label: "外部数据接入",
            children: <ExternalDataSourcesPage />,
          },
        ]}
      />
    </main>
  );
}
