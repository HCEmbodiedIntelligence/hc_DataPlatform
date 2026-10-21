import { Link, useLocation } from "react-router-dom";
import { safeReturnTo } from "./navigation";
/** Legacy raw-processing links now lead to association management, never an instance-owned editor. */
export function robotConfigPath(
  robotId: string,
  back: string,
  scheme?: string,
) {
  const params = new URLSearchParams({ robot: robotId, section: "processing" });
  if (scheme) params.set("scheme", scheme);
  const safe = safeReturnTo(back);
  if (safe) params.set("returnTo", safe);
  return `/settings/robot-instances?${params}`;
}
export function RobotProcessingLink({
  robotId,
  scheme,
  sample,
  file,
}: {
  robotId: string;
  scheme?: string;
  sample?: string;
  file?: string;
}) {
  const location = useLocation(),
    params = new URLSearchParams(location.search);
  if (sample) params.set("rawSource", sample);
  const back = location.pathname + (params.size ? `?${params}` : "");
  const target = new URL(
    robotConfigPath(robotId, back, scheme),
    "https://capture.invalid",
  );
  if (sample) target.searchParams.set("sample", sample);
  if (file) target.searchParams.set("file", file);
  return <Link to={target.pathname + target.search}>管理实例采集方案</Link>;
}
