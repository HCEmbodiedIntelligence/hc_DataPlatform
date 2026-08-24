import { useRef } from 'react';

export interface DetailTab {
  id: string;
  label: string;
  disabled?: boolean;
}

export interface DetailTabsProps {
  tabs: readonly DetailTab[];
  activeTab: string;
  onChange: (tabId: string) => void;
  label?: string;
  /** Supply only when each referenced tabpanel is present in the DOM. */
  panelIdForTab?: (tabId: string) => string | undefined;
}

export function DetailTabs({
  tabs,
  activeTab,
  onChange,
  label = '详情标签页',
  panelIdForTab,
}: DetailTabsProps) {
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const enabledTabs = tabs.filter((tab) => !tab.disabled);
  const focusableTab = enabledTabs.some((tab) => tab.id === activeTab)
    ? activeTab
    : enabledTabs[0]?.id;
  const move = (from: string, delta: number) => {
    const current = enabledTabs.findIndex((tab) => tab.id === from);
    const target = enabledTabs[(current + delta + enabledTabs.length) % enabledTabs.length];
    if (target) {
      onChange(target.id);
      refs.current.get(target.id)?.focus();
    }
  };
  const moveToEdge = (last: boolean) => {
    const target = last ? enabledTabs.at(-1) : enabledTabs[0];
    if (target) {
      onChange(target.id);
      refs.current.get(target.id)?.focus();
    }
  };
  return (
    <div className="detail-tabs" role="tablist" aria-label={label}>
      {tabs.map((tab) => {
        const panelId = panelIdForTab?.(tab.id);
        return (
          <button
            key={tab.id}
            ref={(node) => {
              if (node) refs.current.set(tab.id, node);
              else refs.current.delete(tab.id);
            }}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={activeTab === tab.id}
            {...(panelId ? { 'aria-controls': panelId } : {})}
            tabIndex={focusableTab === tab.id ? 0 : -1}
            disabled={tab.disabled}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight') { event.preventDefault(); move(tab.id, 1); }
              if (event.key === 'ArrowLeft') { event.preventDefault(); move(tab.id, -1); }
              if (event.key === 'Home') { event.preventDefault(); moveToEdge(false); }
              if (event.key === 'End') { event.preventDefault(); moveToEdge(true); }
            }}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
