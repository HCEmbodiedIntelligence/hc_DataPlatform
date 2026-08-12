export * from './PageHeader';
export * from './StandardTable';
export * from './FilterBar';
export * from './CursorPager';
export * from './StatusBadge';
export * from './EmptyState';
export * from './SkeletonBlock';
export * from './ErrorPanel';
export * from './ForbiddenPanel';
export * from './ConfirmDialog';
export * from './SideDrawer';
export * from './DetailTabs';
export * from './MetricCard';
export * from './RelativeTime';
export * from './CopyableId';

// Stage-one UI primitives. Three names intentionally use compatibility aliases
// until their legacy root exports have no remaining page consumers.
export {
  DetailPageScaffold,
  EntityDrawer,
  FilterToolbar,
  PageHeader as UiPageHeader,
  StandardPageScaffold,
  WorkbenchScaffold,
} from './layout';
export type {
  DetailPageScaffoldProps,
  EntityDrawerProps,
  FilterToolbarProps,
  PageBreadcrumbItem,
  PageHeaderProps as UiPageHeaderProps,
  StandardPageScaffoldProps,
  WorkbenchScaffoldProps,
} from './layout';
export { MetricCard as UiMetricCard, PAGE_STATE_KINDS, PageState, StatusTag } from './state';
export type {
  MetricCardProps as UiMetricCardProps,
  MetricState,
  PageStateKind,
  PageStateProps,
  StatusTagProps,
  StatusTone,
} from './state';
export { CursorPager as DataCursorPager, DataTable } from './data';
export type {
  CursorPageInfo,
  CursorPagerProps as DataCursorPagerProps,
  CursorRequest,
  DataTableProps,
  DataTableSelection,
  DataTableState,
} from './data';
export * from './forms';
export * from './actions';
