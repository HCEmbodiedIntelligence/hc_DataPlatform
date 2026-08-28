import { Navigate } from "react-router-dom";
import { useShellStore } from "../../shared/scope/shell-store";

/** Compatibility redirect for bookmarks created before the shared platform landing. */
export function EmptyAccountRoute() {
  const signedIn = useShellStore(
    (state) => state.principal !== null && state.sessionToken !== null,
  );
  return <Navigate replace to={signedIn ? "/" : "/auth/login"} />;
}
