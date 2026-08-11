import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Outlet } from 'react-router-dom';
import { cancelActiveTransports } from '../../shared/api/transport-lifecycle';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useShellStore } from '../../shared/scope/shell-store';
import { ForbiddenPanel } from '../../shared/ui/ForbiddenPanel';
import { SkeletonBlock } from '../../shared/ui/SkeletonBlock';

export function RouteCapabilityGuard({ requiredCapabilities }: { requiredCapabilities: readonly string[] }) {
  const authorization = useCapabilities();
  const scopeKey = useShellStore((state) => state.scopeKey);
  const queryClient = useQueryClient();
  const requiresAuthorization = requiredCapabilities.length > 0;
  const allowed = requiredCapabilities.every(authorization.has);
  const blocked = requiresAuthorization && (authorization.failed || !allowed);

  useEffect(() => {
    if (!blocked) return;
    void queryClient.cancelQueries({ predicate: (query) => query.queryKey[1] === scopeKey });
    queryClient.removeQueries({ predicate: (query) => query.queryKey[1] === scopeKey });
    void cancelActiveTransports();
  }, [blocked, queryClient, scopeKey]);

  if (requiresAuthorization && authorization.loading) {
    return <SkeletonBlock width="100%" height={180} label="正在验证页面权限" />;
  }
  if (blocked) {
    return <ForbiddenPanel description="当前授权快照不允许读取此页面；页面未发起领域请求。" />;
  }
  return <Outlet />;
}
