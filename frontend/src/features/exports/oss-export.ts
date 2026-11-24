import { z } from "zod";
import { request } from "../../shared/api/http-client";
import type { Scope } from "../../shared/scope/model";
import { waitForDeliveryCache } from "../../shared/lib/delivery-cache";

export type ExportDestination = "zip" | "oss";
export type OssReceipt = { kind: "oss"; uri: string; bytes: number };

const transferEvent = z.union([
  z.object({ error: z.string() }),
  z.object({
    uploaded_bytes: z.number().int().nonnegative(),
    oss_uri: z
      .string()
      .regex(/^oss:\/\/[^/?#]+\/[^?#]+\.zip$/iu)
      .optional(),
  }),
]);

export async function exportToOss(
  scope: Scope,
  body: Record<string, unknown>,
  signal: AbortSignal,
  onProgress: (bytes: number) => void,
): Promise<OssReceipt> {
  if (!scope.organizationId || !scope.projectId || !scope.regionCode)
    throw new Error("请选择组织、项目和区域。");
  const path = `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/episode-deliveries/oss`;
  return waitForDeliveryCache(async () => {
    const decoder = new TextDecoder();
    let pending = "";
    let receipt: OssReceipt | undefined;
    let error: string | undefined;
    function processLine(line: string) {
      const event = transferEvent.parse(JSON.parse(line));
      if ("error" in event) error = event.error;
      else {
        onProgress(event.uploaded_bytes);
        if (event.oss_uri)
          receipt = {
            kind: "oss",
            uri: event.oss_uri,
            bytes: event.uploaded_bytes,
          };
      }
    }
    const sink = new WritableStream<Uint8Array>({
      write(chunk) {
        const lines = (pending + decoder.decode(chunk, { stream: true })).split(
          "\n",
        );
        pending = lines.pop()!;
        lines.forEach(processLine);
      },
      close() {
        pending += decoder.decode();
        if (pending) processLine(pending);
      },
    });
    await request({
      method: "POST",
      path,
      scope,
      scopeMode: "explicit",
      body,
      signal,
      cache: "no-store",
      downloadTo: sink,
    });
    if (error || !receipt)
      throw new Error(error ?? "尚未收到 OSS 导出地址，请重试。");
    return receipt;
  }, signal);
}
