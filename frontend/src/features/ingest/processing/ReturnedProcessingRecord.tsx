import { Drawer } from "antd";
import { useSearchParams } from "react-router-dom";
import { useIngestScope } from "../use-ingest-scope";
import ResumeProcessingAction from "./ResumeProcessingAction";

/** Restore the exact batch after returning from organization-level robot settings. */
export default function ReturnedProcessingRecord() {
  const [params, setParams] = useSearchParams();
  const scope = useIngestScope();
  const raw = params.get("rawSource");
  if (!raw || !scope) return null;
  return (
    <Drawer
      open
      title="原始数据处理记录"
      size="large"
      destroyOnHidden
      onClose={() => {
        const next = new URLSearchParams(params);
        next.delete("rawSource");
        setParams(next, { replace: true });
      }}
    >
      <ResumeProcessingAction scope={scope} importId={raw} />
    </Drawer>
  );
}
