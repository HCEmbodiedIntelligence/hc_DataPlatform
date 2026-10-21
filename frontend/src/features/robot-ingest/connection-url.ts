import type { RuntimeConfig } from "../../shared/config/runtime";

/** Pairing links are opened by the robot, which may be on another machine. */
export function robotConnectionBaseUrl(
  config: Pick<RuntimeConfig, "apiBaseUrl" | "robotPlatformBaseUrl">,
  browserOrigin: string,
): URL {
  const configured = config.robotPlatformBaseUrl?.trim();
  let base: URL;
  try {
    base = configured
      ? new URL(configured)
      : new URL(config.apiBaseUrl, browserOrigin);
    if (
      !["http:", "https:"].includes(base.protocol) ||
      !base.hostname ||
      base.username ||
      base.password ||
      base.search ||
      base.hash
    )
      throw new Error();
  } catch {
    throw new Error(
      "平台连接地址无效，请配置机器人可访问的 HTTP(S) 平台地址。",
    );
  }
  const host = base.hostname.replace(/\.$/u, "");
  if (
    host === "localhost" ||
    host.endsWith(".localhost") ||
    /^127\./u.test(host) ||
    ["0.0.0.0", "[::]", "[::1]"].includes(host) ||
    /^\[::ffff:7f[0-9a-f]{2}:/u.test(host)
  ) {
    throw new Error(
      "当前平台地址仅能本机访问。请通过机器人可访问的局域网 IP 或域名打开平台后重试，或由管理员配置平台对外地址。",
    );
  }
  base.pathname = base.pathname
    .replace(/\/api\/v1\/?$/u, "")
    .replace(/\/+$/u, "");
  return base;
}
