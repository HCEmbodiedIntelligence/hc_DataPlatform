import { lazy, Suspense, useState } from "react";
import { Card, Select } from "antd";
import type { Scope } from "../../shared/scope/model";
import type { Member } from "./api";
const OriginalFileExplorer = lazy(
  () => import("../../features/ingest/browser/OriginalFileExplorer"),
);
export function RawFiles({ scope, member }: { scope: Scope; member: Member }) {
  const [source, setSource] = useState(member.raw_source_ids[0]);
  return (
    <Card size="small" title="原始上传文件">
      <p>该上传尚无关联 Episode。上传 ID 为 {member.upload_id}。</p>
      {member.raw_source_ids.length > 1 && (
        <Select
          aria-label="原始来源"
          value={source}
          onChange={setSource}
          options={member.raw_source_ids.map((id) => ({
            value: id,
            label: id,
          }))}
        />
      )}
      {source && scope.projectId && scope.regionCode && (
        <Suspense fallback={<p>正在加载文件…</p>}>
          <OriginalFileExplorer
            scope={{
              organizationId: scope.organizationId,
              projectId: scope.projectId,
              regionCode: scope.regionCode,
            }}
            importId={source}
            canDownload={false}
          />
        </Suspense>
      )}
    </Card>
  );
}
