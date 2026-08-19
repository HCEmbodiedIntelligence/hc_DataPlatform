import { Navigate, useLocation, useParams } from "react-router-dom";
import { PageState } from "../../shared/ui/state/PageState";
import {
  buildAnnotationCompatibilityTarget,
  buildCleaningCompatibilityTarget,
  buildUploadRecordsCompatibilityTarget,
} from "./navigation-routes";
import styles from "./RouteCompatibility.module.css";

function FeatureUnavailableRoute({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <section
      className={styles.featureRoute}
      data-route-state="feature-unavailable"
    >
      <header className={styles.featureHeader}>
        <p className={styles.featureEyebrow}>{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </header>
      <PageState
        state="feature-unavailable"
        label={title}
        title="能力尚未开放"
        description="当前环境没有此模式的完整业务合同，因此不加载旧页面，也不展示半成品操作或伪造成功结果。"
      />
    </section>
  );
}

export function LegacyUploadIndexRedirect() {
  const location = useLocation();
  return (
    <Navigate
      replace
      to={buildUploadRecordsCompatibilityTarget(location.search, location.hash)}
    />
  );
}

export function LegacyAnnotationIndexRedirect() {
  const location = useLocation();
  return (
    <Navigate
      replace
      to={buildAnnotationCompatibilityTarget(location.search, location.hash)}
    />
  );
}

export function LegacyCleaningDraftsRedirect() {
  const location = useLocation();
  return (
    <Navigate
      replace
      to={buildCleaningCompatibilityTarget(location.search, location.hash)}
    />
  );
}

export function LegacyCleaningWorkbenchRedirect() {
  const location = useLocation();
  const { draftId } = useParams<{ draftId: string }>();
  return (
    <Navigate
      replace
      to={buildCleaningCompatibilityTarget(
        location.search,
        location.hash,
        draftId,
      )}
    />
  );
}

export function AnnotationRevisionUnavailableRoute() {
  return (
    <FeatureUnavailableRoute
      eyebrow="数据标注 / 数据修订"
      title="数据标注"
      description="历史清洗入口已迁移到数据标注的数据修订模式；当前修订业务合同尚未实现。"
    />
  );
}

export function AnnotationTagReviewUnavailableRoute() {
  return (
    <FeatureUnavailableRoute
      eyebrow="数据标注 / Tag 审核"
      title="数据标注"
      description="Tag 审核属于数据标注的同一功能模式；当前审核业务合同尚未实现。"
    />
  );
}
