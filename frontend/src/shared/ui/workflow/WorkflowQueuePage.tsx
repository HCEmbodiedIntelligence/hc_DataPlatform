import type { JSX, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { ArrowRight, Check, Search } from "lucide-react";
import { Link } from "react-router-dom";
import styles from "./WorkflowQueuePage.module.css";

export interface WorkflowQueueStage<Key extends string> {
  readonly key: Key;
  readonly label: string;
  readonly description: string;
  readonly queueDescription: string;
  readonly emptyDescription: string;
  readonly icon: LucideIcon;
  readonly count: number;
  readonly href: string;
}

export interface WorkflowQueueSearch {
  readonly id: string;
  readonly name: string;
  readonly label: string;
  readonly placeholder: string;
  readonly value: string;
  readonly meta: string;
  readonly onChange: (value: string) => void;
}

export interface WorkflowQueuePageProps<Key extends string> {
  readonly title: string;
  readonly headerActions?: ReactNode;
  readonly stages: readonly WorkflowQueueStage<Key>[];
  readonly selectedStage: Key;
  readonly stageNavigationLabel: string;
  readonly search: WorkflowQueueSearch;
  readonly visibleCount: number;
  readonly resultLabel?: string;
  readonly tableLabel: string;
  readonly table: ReactNode;
  readonly emptyTitle?: string;
  readonly emptyDescription?: string;
  readonly emptyActionLabel: string;
  readonly emptyActionDisabled?: boolean;
  readonly onEmptyAction: () => void;
}

/**
 * Shared queue shell for state-driven data workflows. Business pages provide
 * status definitions and columns; navigation, search, empty state and table
 * presentation stay identical across annotation, segmentation and review.
 */
export function WorkflowQueuePage<Key extends string>({
  emptyActionDisabled = false,
  resultLabel = "项结果",
  ...props
}: WorkflowQueuePageProps<Key>): JSX.Element {
  const currentStage =
    props.stages.find((stage) => stage.key === props.selectedStage) ??
    props.stages[0];
  if (!currentStage) {
    throw new Error("WorkflowQueuePage requires at least one stage");
  }
  const CurrentStageIcon = currentStage.icon;
  const hasSearch = props.search.value.trim().length > 0;

  return (
    <main className={styles.page}>
      <header className={styles.pageHeader}>
        <h1>{props.title}</h1>
        {props.headerActions ? (
          <div className={styles.headerActions}>{props.headerActions}</div>
        ) : null}
      </header>

      <nav className={styles.stageGrid} aria-label={props.stageNavigationLabel}>
        {props.stages.map((stage) => {
          const Icon = stage.icon;
          const selected = stage.key === currentStage.key;
          return (
            <Link
              aria-current={selected ? "page" : undefined}
              aria-label={`${stage.label}，${stage.description}，${stage.count} 项${selected ? "，当前队列" : ""}`}
              className={styles.stageCard}
              key={stage.key}
              to={stage.href}
            >
              <span className={styles.stageIcon}>
                <Icon aria-hidden="true" size={20} strokeWidth={1.8} />
              </span>
              <span className={styles.stageCopy}>
                <strong>{stage.label}</strong>
                <span>{stage.description}</span>
              </span>
              <strong className={styles.stageCount}>{stage.count}</strong>
              <span className={styles.stageState} aria-hidden="true">
                {selected ? (
                  <>
                    <Check size={13} strokeWidth={2.2} />
                    当前队列
                  </>
                ) : (
                  <ArrowRight size={15} />
                )}
              </span>
            </Link>
          );
        })}
      </nav>

      <section className={styles.queuePanel} aria-labelledby={`${props.search.id}-queue-title`}>
        <header className={styles.queueHeader}>
          <div>
            <p>当前队列</p>
            <h2 id={`${props.search.id}-queue-title`}>{currentStage.label}</h2>
            <span>{currentStage.queueDescription}</span>
          </div>
          <output aria-live="polite">
            <strong>{props.visibleCount}</strong>
            <span>{resultLabel}</span>
          </output>
        </header>

        <div className={styles.queueToolbar} role="search">
          <label htmlFor={props.search.id}>
            <span>{props.search.label}</span>
            <span className={styles.searchField}>
              <Search aria-hidden="true" size={17} />
              <input
                autoComplete="off"
                id={props.search.id}
                name={props.search.name}
                placeholder={props.search.placeholder}
                type="search"
                value={props.search.value}
                onChange={(event) => props.search.onChange(event.target.value)}
              />
            </span>
          </label>
          <span>{props.search.meta}</span>
        </div>

        {props.visibleCount === 0 ? (
          <div className={styles.queueEmpty}>
            <span className={styles.emptyIcon}>
              <CurrentStageIcon aria-hidden="true" size={22} />
            </span>
            <h3>
              {props.emptyTitle ??
                (hasSearch
                  ? "没有匹配的任务"
                  : `${currentStage.label}队列为空`)}
            </h3>
            <p>
              {props.emptyDescription ??
                (hasSearch
                  ? "请缩短关键词或清除搜索，任务状态筛选会继续保留。"
                  : currentStage.emptyDescription)}
            </p>
            <button
              disabled={emptyActionDisabled}
              type="button"
              onClick={props.onEmptyAction}
            >
              {props.emptyActionLabel}
            </button>
          </div>
        ) : (
          <div
            aria-label={`${currentStage.label}${props.tableLabel}，可横向滚动`}
            className={styles.tableWrap}
            role="region"
            tabIndex={0}
          >
            {props.table}
          </div>
        )}
      </section>
    </main>
  );
}

