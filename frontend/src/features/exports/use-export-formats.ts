import { useQuery } from "@tanstack/react-query";
import { fetchExportFormats } from "./api";

/** Explicit scope keeps this reusable across pages and independent workspaces. */
export function useExportFormats(
  scopeKey: string,
  projectId: string | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["export-formats", scopeKey, projectId],
    queryFn: ({ signal }) => fetchExportFormats(projectId!, signal),
    enabled: enabled && Boolean(projectId),
    staleTime: 30_000,
  });
}
