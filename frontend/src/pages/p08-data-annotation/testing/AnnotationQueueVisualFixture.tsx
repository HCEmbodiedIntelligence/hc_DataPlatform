import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { ProviderHarness } from "../../../app/providers";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  AnnotationQueuePage,
  TagReviewQueuePage,
} from "../AnnotationQueuePage";

const visualScope = Object.freeze({
  organizationId: "org_fe13_visual",
  projectId: "project_fe13_visual",
  regionCode: "cn-east-01",
});

const mountedRoots = new WeakMap<HTMLElement, Root>();

export function mountAnnotationQueueVisualFixture(
  host: HTMLElement,
  mode: "annotation" | "tag-review" = "annotation",
): void {
  mountedRoots.get(host)?.unmount();
  const shell = useShellStore.getState();
  shell.setSession(
    {
      actorId: "actor_fe13_visual",
      displayName: "前端复核员",
      roleIds: ["PROJECT_DATA_PROCESSOR"],
    },
    "session-fe13-visual",
  );
  shell.setScope(visualScope);
  shell.setAuthorization({
    scopeKey: useShellStore.getState().scopeKey,
    roleVersion: "role-fe13-visual",
    capabilities: ["annotation_task.read", "annotation_task.claim"],
    fetchedAt: "2026-08-18T10:00:00Z",
  });
  shell.setSessionScopes(
    [
      {
        organizationId: visualScope.organizationId,
        projectId: visualScope.projectId,
        regionCodes: [visualScope.regionCode],
        projectWide: false,
        capabilities: ["annotation_task.read", "annotation_task.claim"],
      },
    ],
    1,
  );

  const root = createRoot(host);
  mountedRoots.set(host, root);
  const Component =
    mode === "annotation" ? AnnotationQueuePage : TagReviewQueuePage;
  root.render(
    <ProviderHarness>
      <MemoryRouter
        initialEntries={[
          mode === "annotation"
            ? "/annotations/annotate"
            : "/annotations/tag-review",
        ]}
      >
        <Component />
      </MemoryRouter>
    </ProviderHarness>,
  );
}
