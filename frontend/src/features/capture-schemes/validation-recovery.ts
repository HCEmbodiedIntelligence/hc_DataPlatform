import { isDomainError } from "../../shared/api/domain-error";
import { createManagedAbortController } from "../../shared/api/transport-lifecycle";
import type { Sample, SchemeDetail } from "./contracts";

const unconfirmedMessage = "暂未查询到样本验证结果，方案已保存。请稍后重新打开方案查看，无需重复保存。";

function responseMayBeLost(error: unknown): boolean {
  return isDomainError(error) && (
    error.code === "NETWORK_ERROR" ||
    [502, 504, 524].includes(error.httpStatus ?? 0)
  );
}

function waitForResult(signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 5000);
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** A timed-out validation may still commit. Read its evidence without resubmitting it. */
export async function validateWithRecovery(
  submit: (signal: AbortSignal) => Promise<SchemeDetail>,
  read: (signal: AbortSignal) => Promise<SchemeDetail>,
  revision: number,
  sample: Sample,
): Promise<SchemeDetail> {
  // Keep cancellation active between requests as well as during each fetch.
  const { controller, release } = createManagedAbortController();
  const { signal } = controller;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  try {
    try {
      const detail = await submit(signal);
      signal.throwIfAborted();
      return detail;
    } catch (error) {
      signal.throwIfAborted();
      if (!responseMayBeLost(error)) throw error;
    }
    deadline = setTimeout(() => controller.abort(new Error(unconfirmedMessage)), 120_000);
    for (let attempt = 0; attempt <= 24; attempt++) {
      if (attempt) await waitForResult(signal);
      let detail: SchemeDetail;
      try {
        detail = await read(signal);
      } catch (error) {
        signal.throwIfAborted();
        if (!responseMayBeLost(error)) throw error;
        continue;
      }
      signal.throwIfAborted();
      if (detail.revision !== revision || detail.archived) {
        throw new Error("方案已更新或归档，请重新打开方案查看当前验证结果。");
      }
      if (detail.validations.some((evidence) =>
        evidence.revision === revision &&
        evidence.source_format === sample.source_format &&
        evidence.sample &&
        Object.entries(sample).every(([key, value]) =>
          evidence.sample?.[key as keyof Sample] === value,
        ),
      )) return detail;
    }
    throw new Error(unconfirmedMessage);
  } finally {
    clearTimeout(deadline);
    release();
  }
}
