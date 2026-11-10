import { Navigate, useLocation } from "react-router-dom";

export function Component() {
  const { search, hash } = useLocation();
  const params = new URLSearchParams(search);
  const robotId = params.get("robotId");
  if (robotId) {
    params.delete("robotId");
    params.set("robot", robotId);
    return (
      <Navigate
        replace
        to={{
          pathname: "/settings/robot-instances",
          search: params.toString(),
          hash,
        }}
      />
    );
  }
  return (
    <Navigate
      replace
      to={{ pathname: "/settings/robot-models", search, hash }}
    />
  );
}

export default Component;
