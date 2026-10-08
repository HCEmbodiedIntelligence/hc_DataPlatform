import { useLocation, useParams, useSearchParams } from "react-router-dom";
import UploadDetailView from "../../features/ingest/detail/UploadDetailView";

export default function RawDiagnosticRoute() {
  const { uploadId = "" } = useParams();
  const { pathname } = useLocation();
  const [search] = useSearchParams();
  return (
    <UploadDetailView
      uploadId={uploadId}
      pathname={pathname}
      returnTo={search.get("returnTo")}
    />
  );
}
