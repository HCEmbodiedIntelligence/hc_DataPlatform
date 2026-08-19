import { isDomainError } from "../../shared/api/domain-error";
import type { UploadManifest } from "./formal-client";

export const MAX_MANIFEST_BYTES = 1024 * 1024;
export const MAX_PACKAGE_BYTES = 5 * 1024 ** 4;
export const MIN_MULTIPART_BYTES = 5 * 1024 ** 2;
export const MAX_MULTIPART_PARTS = 10_000;
export const AUTHORIZATION_BATCH_SIZE = 256;

const CRC64_DECIMAL = /^(?:0|[1-9][0-9]{0,19})$/u;
const CRC64_MAX = 18_446_744_073_709_551_615n;

export interface UploadPartPlan {
  readonly partSize: number;
  readonly partCount: number;
  readonly partNumbers: readonly number[];
}

export interface UploadProblemCopy {
  readonly title: string;
  readonly detail: string;
  readonly requestId: string | null;
  readonly retryable: boolean;
  readonly status: number | null;
  readonly problemCode: string | null;
}

export class LocalManifestError extends Error {
  constructor(
    readonly code: "MANIFEST_EMPTY" | "MANIFEST_TOO_LARGE" | "MANIFEST_INVALID",
    message: string,
  ) {
    super(message);
    this.name = "LocalManifestError";
  }
}

function assertDecimalCrc64(
  value: Record<string, unknown>,
  location: string,
): void {
  if (!("crc64" in value)) return;
  const crc64 = value.crc64;
  if (
    typeof crc64 !== "string" ||
    !CRC64_DECIMAL.test(crc64) ||
    BigInt(crc64) > CRC64_MAX
  ) {
    throw new LocalManifestError(
      "MANIFEST_INVALID",
      `${location} 必须是 uint64 十进制字符串；不能使用会丢失精度的 JSON 数字。`,
    );
  }
}

function validateManifestCrc64Values(raw: Record<string, unknown>): void {
  assertDecimalCrc64(raw, "Manifest crc64");
  if (!Array.isArray(raw.files)) return;
  raw.files.forEach((item, index) => {
    if (typeof item === "object" && item !== null && !Array.isArray(item)) {
      assertDecimalCrc64(
        item as Record<string, unknown>,
        `Manifest files[${index}].crc64`,
      );
    }
  });
}

export function planUploadParts(size: number): UploadPartPlan {
  if (!Number.isSafeInteger(size) || size <= 0 || size > MAX_PACKAGE_BYTES) {
    throw new RangeError("数据包大小必须在 1 字节到 5 TiB 之间。");
  }
  const partSize = Math.max(
    MIN_MULTIPART_BYTES,
    Math.ceil(size / MAX_MULTIPART_PARTS),
  );
  const partCount = Math.ceil(size / partSize);
  return {
    partSize,
    partCount,
    partNumbers: Array.from({ length: partCount }, (_, index) => index + 1),
  };
}

export function partBounds(
  partNumber: number,
  plan: UploadPartPlan,
  totalBytes: number,
) {
  const start = (partNumber - 1) * plan.partSize;
  return { start, end: Math.min(totalBytes, start + plan.partSize) };
}

export async function parseManifestFile(file: File): Promise<UploadManifest> {
  if (file.size === 0) {
    throw new LocalManifestError(
      "MANIFEST_EMPTY",
      "Manifest 为空；请选择包含 JSON 内容的 Manifest。",
    );
  }
  if (file.size > MAX_MANIFEST_BYTES) {
    throw new LocalManifestError(
      "MANIFEST_TOO_LARGE",
      "Manifest 超过正式合同的 1 MiB 上限。",
    );
  }
  let raw: unknown;
  try {
    const text =
      typeof file.text === "function"
        ? await file.text()
        : await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onerror = () =>
              reject(reader.error ?? new Error("MANIFEST_READ_FAILED"));
            reader.onload = () =>
              resolve(typeof reader.result === "string" ? reader.result : "");
            reader.readAsText(file, "utf-8");
          });
    raw = JSON.parse(text) as unknown;
  } catch {
    throw new LocalManifestError(
      "MANIFEST_INVALID",
      "Manifest 必须是有效的 UTF-8 JSON。",
    );
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new LocalManifestError(
      "MANIFEST_INVALID",
      "Manifest 根节点必须是 JSON 对象。",
    );
  }
  validateManifestCrc64Values(raw as Record<string, unknown>);
  // The production preflight endpoint performs the complete generated-schema validation.
  // This cast only bridges untrusted JSON into that typed request; it does not assert validity.
  return raw as UploadManifest;
}

export function findManifestFile(files: readonly File[]): File | null {
  const jsonFiles = files.filter((file) =>
    file.name.toLowerCase().endsWith(".json"),
  );
  return (
    jsonFiles.find((file) =>
      /(^|[_-])(rollout[_-]?)?manifest/i.test(file.name),
    ) ?? (jsonFiles.length === 1 ? (jsonFiles[0] ?? null) : null)
  );
}

export function findRawPackageFile(
  files: readonly File[],
  manifest: UploadManifest,
): File | null {
  const raw = manifest.files?.find((item) => item.role === "RAW_MCAP");
  if (!raw) return null;
  const expectedName = raw.path.split("/").at(-1);
  return (
    files.find((file) => {
      const relative = file.webkitRelativePath || file.name;
      return relative === raw.path || file.name === expectedName;
    }) ?? null
  );
}

export function validateObjectStorageUri(value: string): string | null {
  const normalized = value.trim();
  if (!normalized) return "请输入平台已授权的对象地址。";
  if (normalized.length > 2048) return "对象地址超过 2048 字符上限。";
  let parsed: URL;
  try {
    parsed = new URL(normalized);
  } catch {
    return "对象地址格式无效。";
  }
  if (!["s3:", "oss:"].includes(parsed.protocol))
    return "仅支持 s3:// 或 oss:// 授权对象地址。";
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    return "对象地址不能包含凭据、签名参数或片段。";
  }
  if (!parsed.hostname || !parsed.pathname || parsed.pathname === "/")
    return "对象地址必须包含存储桶和对象路径。";
  return null;
}

export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"] as const;
  let amount = value;
  let unit: (typeof units)[number] = units[0];
  for (const candidate of units.slice(1)) {
    if (amount < 1024) break;
    amount /= 1024;
    unit = candidate;
  }
  return `${amount >= 100 || unit === "B" ? amount.toFixed(0) : amount.toFixed(1)} ${unit}`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0)
    return "计算中";
  const rounded = Math.ceil(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remaining = rounded % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分 ${remaining} 秒`;
  return `${remaining} 秒`;
}

export function uploadProblemCopy(error: unknown): UploadProblemCopy {
  if (error instanceof LocalManifestError) {
    return {
      title:
        error.code === "MANIFEST_EMPTY"
          ? "未发现可用 Manifest"
          : "Manifest 预检失败",
      detail: error.message,
      requestId: null,
      retryable: false,
      status: error.code === "MANIFEST_TOO_LARGE" ? 413 : 422,
      problemCode: error.code,
    };
  }
  if (!isDomainError(error)) {
    return {
      title: "上传操作未完成",
      detail: "未收到可安全解释的服务端结果，请刷新事实后重试。",
      requestId: null,
      retryable: false,
      status: null,
      problemCode: null,
    };
  }
  const statusCopy: Readonly<
    Record<number, { title: string; detail: string }>
  > = {
    403: {
      title: "当前授权不能上传",
      detail: "请确认项目、区域和上传权限后重试。",
    },
    409: {
      title: "数据包事实发生冲突",
      detail:
        "该数据包标识、内容或来源已存在冲突，请核对 Manifest 和上传记录。",
    },
    422: {
      title: "Manifest 预检失败",
      detail: "Manifest 内容不满足正式上传合同，请根据错误码修正后重试。",
    },
    429: {
      title: "请求频率受限",
      detail: "请按服务端重试提示等待，不要连续重复提交。",
    },
  };
  const fallback = statusCopy[error.httpStatus ?? 0];
  return {
    title:
      fallback?.title ??
      (error.httpStatus === 413 ? "数据包超过限制" : "上传操作未完成"),
    detail: error.message || fallback?.detail || "服务端拒绝了本次操作。",
    requestId: error.requestId,
    retryable: error.retryable,
    status: error.httpStatus,
    problemCode: error.problemCode,
  };
}
