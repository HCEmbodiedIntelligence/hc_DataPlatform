import { Button, Input, Modal, Tag, type InputRef } from "antd";
import {
  Bot,
  CornerDownLeft,
  Database,
  FolderOpen,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { isDomainError } from "../../shared/api/domain-error";
import { PageState } from "../../shared/ui";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { routes } from "../ingest/routing";
import { routes as datasetRoutes } from "../datasets/routing";
import {
  useGlobalDataSourceSearch,
  useGlobalDatasetSearch,
  useGlobalRobotSearch,
} from "./api";
import styles from "./GlobalRobotSearchDialog.module.css";

interface GlobalRobotSearchDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
}

function errorState(
  error: unknown,
): "forbidden" | "offline" | "rate-limited" | "contract-mismatch" | "error" {
  if (!isDomainError(error)) return "contract-mismatch";
  if (
    error.code === "FORBIDDEN" ||
    error.problemCode === "CAPABILITY_REQUIRED" ||
    error.problemCode === "REGION_SCOPE_DENIED"
  )
    return "forbidden";
  if (error.code === "NETWORK_ERROR") return "offline";
  if (error.code === "RATE_LIMITED") return "rate-limited";
  if (
    error.code === "CONTRACT_MISMATCH" ||
    error.problemCode === "INVALID_CURSOR"
  )
    return "contract-mismatch";
  return "error";
}

function enumText(value: string | { readonly raw: string }): string {
  return typeof value === "string" ? value : value.raw;
}

export default function GlobalRobotSearchDialog({
  open,
  onClose,
}: Readonly<GlobalRobotSearchDialogProps>) {
  const [input, setInput] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const inputRef = useRef<InputRef>(null);
  const navigate = useNavigate();
  const capabilities = useCapabilities();
  const canSearchRobots = capabilities.has("robot.read");
  const canSearchDataSources = capabilities.has("ingest_source.read");
  const canSearchDatasets = capabilities.has("dataset.read");
  const canSearch =
    canSearchRobots || canSearchDataSources || canSearchDatasets;
  const robotResults = useGlobalRobotSearch(
    submittedQuery,
    open && canSearchRobots,
  );
  const dataSourceResults = useGlobalDataSourceSearch(
    submittedQuery,
    open && canSearchDataSources,
  );
  const datasetResults = useGlobalDatasetSearch(
    submittedQuery,
    open && canSearchDatasets,
  );
  const robots = useMemo(
    () => robotResults.data?.pages.flatMap((page) => page.items) ?? [],
    [robotResults.data?.pages],
  );
  const dataSources = useMemo(
    () => dataSourceResults.data?.pages.flatMap((page) => page.items) ?? [],
    [dataSourceResults.data?.pages],
  );
  const datasets = useMemo(
    () => datasetResults.data?.pages.flatMap((page) => page.items) ?? [],
    [datasetResults.data?.pages],
  );

  useEffect(() => {
    if (!open) return;
    const timer = globalThis.setTimeout(() => inputRef.current?.focus(), 0);
    return () => globalThis.clearTimeout(timer);
  }, [open]);

  const submit = () => {
    const next = input.trim();
    if (!next || !canSearch) return;
    setSubmittedQuery(next);
  };
  const selectRobot = (robotId: string) => {
    onClose();
    navigate(`/settings/robots?robotId=${encodeURIComponent(robotId)}`);
  };
  const selectDataSource = (sourceId: string) => {
    onClose();
    navigate(routes.sources.build({ sourceId }));
  };
  const selectDataset = (
    datasetId: Parameters<
      typeof datasetRoutes.datasetDetail.build
    >[0]["datasetId"],
  ) => {
    onClose();
    navigate(datasetRoutes.datasetDetail.build({ datasetId }));
  };
  const hasSearched = submittedQuery.length > 0;
  const pageState = capabilities.loading
    ? "loading"
    : !canSearch
      ? "forbidden"
      : null;
  const categories = [
    canSearchRobots ? "机器人" : null,
    canSearchDataSources ? "数据源" : null,
    canSearchDatasets ? "数据集" : null,
  ]
    .filter((item): item is string => item !== null)
    .join(" / ");

  return (
    <Modal
      className={styles.modal}
      destroyOnHidden
      footer={null}
      maskTransitionName=""
      open={open}
      title="全局搜索"
      transitionName=""
      width={640}
      onCancel={onClose}
    >
      <form
        className={styles.searchForm}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Input
          ref={inputRef}
          aria-label="搜索机器人、数据源和数据集"
          disabled={capabilities.loading || !canSearch}
          maxLength={256}
          placeholder="输入名称、序列号或来源 ID"
          prefix={<Search aria-hidden="true" size={16} />}
          value={input}
          onChange={(event) => setInput(event.target.value)}
        />
        <Button
          disabled={
            input.trim().length === 0 || capabilities.loading || !canSearch
          }
          htmlType="submit"
          icon={<CornerDownLeft aria-hidden="true" size={15} />}
          loading={
            (robotResults.isFetching && !robotResults.isFetchingNextPage) ||
            (dataSourceResults.isFetching &&
              !dataSourceResults.isFetchingNextPage) ||
            (datasetResults.isFetching && !datasetResults.isFetchingNextPage)
          }
          type="primary"
        >
          搜索
        </Button>
      </form>
      <div className={styles.hint}>
        <span>当前范围：{categories}</span>
        <span>结果只显示你有权限查看的实体</span>
      </div>
      <section
        aria-live="polite"
        aria-label="搜索结果"
        className={styles.resultRegion}
      >
        {pageState ? (
          <PageState state={pageState} label="全局搜索" />
        ) : !hasSearched ? (
          <div className={styles.intro}>
            <div>
              <strong>检索当前范围内的实体</strong>
              <span>按名称、序列号或实体 ID 定位机器人、数据源和数据集。</span>
            </div>
          </div>
        ) : (
          <div className={styles.lanes}>
            {canSearchRobots ? (
              <section aria-label="机器人搜索结果" className={styles.lane}>
                <div className={styles.laneHeader}>
                  <span className={styles.laneTitle}>
                    <Bot aria-hidden="true" size={15} />
                    机器人
                  </span>
                  <span>物理设备</span>
                </div>
                {robotResults.isPending ? (
                  <PageState state="loading" label="机器人搜索" />
                ) : robotResults.isError ? (
                  <PageState
                    state={errorState(robotResults.error)}
                    label="机器人搜索"
                    requestId={
                      isDomainError(robotResults.error)
                        ? robotResults.error.requestId
                        : null
                    }
                    onRetry={() => void robotResults.refetch()}
                  />
                ) : robots.length === 0 ? (
                  <div className={styles.empty}>
                    <div>
                      <strong>没有匹配的机器人</strong>
                      <span>检查名称、序列号或当前作用域后重新搜索。</span>
                    </div>
                  </div>
                ) : (
                  <div className={styles.results}>
                    {robots.map((item) => (
                      <button
                        className={styles.result}
                        key={item.robot.id}
                        type="button"
                        onClick={() => selectRobot(item.robot.id)}
                      >
                        <span className={styles.resultIcon}>
                          <Bot aria-hidden="true" size={17} />
                        </span>
                        <span className={styles.resultCopy}>
                          <span className={styles.resultName}>
                            {item.robot.display_name}
                          </span>
                          <span className={styles.resultMeta}>
                            {item.robot.serial_no} ·{" "}
                            {item.robot.connectivity.state}
                          </span>
                        </span>
                        <Tag>{item.robot.lifecycle_status}</Tag>
                      </button>
                    ))}
                    {robotResults.hasNextPage ? (
                      <div className={styles.loadMore}>
                        <Button
                          loading={robotResults.isFetchingNextPage}
                          onClick={() => void robotResults.fetchNextPage()}
                        >
                          加载更多机器人
                        </Button>
                      </div>
                    ) : null}
                  </div>
                )}
              </section>
            ) : null}
            {canSearchDataSources ? (
              <section aria-label="数据源搜索结果" className={styles.lane}>
                <div className={styles.laneHeader}>
                  <span className={styles.laneTitle}>
                    <Database aria-hidden="true" size={15} />
                    数据源
                  </span>
                  <span>接入来源</span>
                </div>
                {dataSourceResults.isPending ? (
                  <PageState state="loading" label="数据源搜索" />
                ) : dataSourceResults.isError ? (
                  <PageState
                    state={errorState(dataSourceResults.error)}
                    label="数据源搜索"
                    requestId={
                      isDomainError(dataSourceResults.error)
                        ? dataSourceResults.error.requestId
                        : null
                    }
                    onRetry={() => void dataSourceResults.refetch()}
                  />
                ) : dataSources.length === 0 ? (
                  <div className={styles.empty}>
                    <div>
                      <strong>没有匹配的数据源</strong>
                      <span>检查名称、来源 ID 或当前作用域后重新搜索。</span>
                    </div>
                  </div>
                ) : (
                  <div className={styles.results}>
                    {dataSources.map((source) => (
                      <button
                        className={styles.result}
                        key={source.id}
                        type="button"
                        onClick={() => selectDataSource(source.id)}
                      >
                        <span
                          className={`${styles.resultIcon} ${styles.sourceIcon}`}
                        >
                          <Database aria-hidden="true" size={17} />
                        </span>
                        <span className={styles.resultCopy}>
                          <span className={styles.resultName}>
                            {source.name}
                          </span>
                          <span className={styles.resultMeta}>
                            {enumText(source.sourceType)} ·{" "}
                            {enumText(source.connectivity.state)}
                          </span>
                        </span>
                        <Tag>{enumText(source.administrativeState)}</Tag>
                      </button>
                    ))}
                    {dataSourceResults.hasNextPage ? (
                      <div className={styles.loadMore}>
                        <Button
                          loading={dataSourceResults.isFetchingNextPage}
                          onClick={() => void dataSourceResults.fetchNextPage()}
                        >
                          加载更多数据源
                        </Button>
                      </div>
                    ) : null}
                  </div>
                )}
              </section>
            ) : null}
            {canSearchDatasets ? (
              <section aria-label="数据集搜索结果" className={styles.lane}>
                <div className={styles.laneHeader}>
                  <span className={styles.laneTitle}>
                    <FolderOpen aria-hidden="true" size={15} />
                    数据集
                  </span>
                  <span>治理资产</span>
                </div>
                {datasetResults.isPending ? (
                  <PageState state="loading" label="数据集搜索" />
                ) : datasetResults.isError ? (
                  <PageState
                    state={errorState(datasetResults.error)}
                    label="数据集搜索"
                    requestId={
                      isDomainError(datasetResults.error)
                        ? datasetResults.error.requestId
                        : null
                    }
                    onRetry={() => void datasetResults.refetch()}
                  />
                ) : datasets.length === 0 ? (
                  <div className={styles.empty}>
                    <div>
                      <strong>没有匹配的数据集</strong>
                      <span>检查名称、数据集 ID 或当前作用域后重新搜索。</span>
                    </div>
                  </div>
                ) : (
                  <div className={styles.results}>
                    {datasets.map((dataset) => (
                      <button
                        className={styles.result}
                        key={dataset.datasetId}
                        type="button"
                        onClick={() => selectDataset(dataset.datasetId)}
                      >
                        <span
                          className={`${styles.resultIcon} ${styles.datasetIcon}`}
                        >
                          <FolderOpen aria-hidden="true" size={17} />
                        </span>
                        <span className={styles.resultCopy}>
                          <span className={styles.resultName}>
                            {dataset.name}
                          </span>
                          <span className={styles.resultMeta}>
                            {dataset.datasetId} · {dataset.episodeCount}{" "}
                            Episodes
                          </span>
                        </span>
                        <Tag>
                          {dataset.currentVersion?.displayVersion ?? "空数据集"}
                        </Tag>
                      </button>
                    ))}
                    {datasetResults.hasNextPage ? (
                      <div className={styles.loadMore}>
                        <Button
                          loading={datasetResults.isFetchingNextPage}
                          onClick={() => void datasetResults.fetchNextPage()}
                        >
                          加载更多数据集
                        </Button>
                      </div>
                    ) : null}
                  </div>
                )}
              </section>
            ) : null}
          </div>
        )}
      </section>
    </Modal>
  );
}
