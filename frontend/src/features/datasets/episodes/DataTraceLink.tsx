import { Link } from "react-router-dom";
import { useCapabilities } from "../../../shared/auth/use-capabilities";

export function DataTraceLink({ identifier }: { identifier: string }) {
  const rights = useCapabilities();
  if (
    !["episode.read", "dataset_version.read", "upload.read"].every(rights.has)
  )
    return null;
  return (
    <Link to={"/data-trace?" + new URLSearchParams({ id: identifier })}>
      回溯同批数据
    </Link>
  );
}
