import { expect, it } from "vitest";
import { robotConnectionBaseUrl } from "./connection-url";

it("uses the configured platform URL even when the page or API uses localhost", () => {
  expect(
    robotConnectionBaseUrl(
      {
        apiBaseUrl: "http://127.0.0.1:8088/api/v1",
        robotPlatformBaseUrl: "http://192.168.1.20:8088/",
      },
      "http://localhost:8088",
    ).toString(),
  ).toBe("http://192.168.1.20:8088/");
});

it.each([
  ["/api/v1", "http://192.168.1.20:8088", "http://192.168.1.20:8088/"],
  [
    "/platform/api/v1/",
    "https://platform.example",
    "https://platform.example/platform",
  ],
  [
    "https://api.example/api/v1",
    "http://localhost:8088",
    "https://api.example/",
  ],
  ["/api/v1", "http://[fd00::20]:8088", "http://[fd00::20]:8088/"],
])(
  "preserves reachable origins and gateway prefixes for %s",
  (apiBaseUrl, browserOrigin, expected) => {
    expect(
      robotConnectionBaseUrl({ apiBaseUrl }, browserOrigin).toString(),
    ).toBe(expected);
  },
);

it.each([
  "localhost",
  "localhost.",
  "robot.localhost",
  "127.0.0.1",
  "127.1.2.3",
  "0.0.0.0",
  "[::1]",
  "[::]",
  "[::ffff:127.0.0.1]",
])("rejects a robot link to %s", (host) => {
  expect(() =>
    robotConnectionBaseUrl({ apiBaseUrl: "/api/v1" }, `http://${host}:8088`),
  ).toThrow("仅能本机访问");
});

it.each([
  "/api/v1",
  "ftp://platform.example",
  "https://user:secret@platform.example",
  "http://platform.example/?secret=value",
  "http://platform.example/#fragment",
])(
  "rejects an invalid configured platform URL instead of silently using the page origin: %s",
  (robotPlatformBaseUrl) => {
    expect(() =>
      robotConnectionBaseUrl(
        { apiBaseUrl: "/api/v1", robotPlatformBaseUrl },
        "https://platform.example",
      ),
    ).toThrow("平台连接地址无效");
  },
);
