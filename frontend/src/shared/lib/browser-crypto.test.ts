import { createHash, webcrypto } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installRandomUuidFallback, sha256File } from "./browser-crypto";

afterEach(() => vi.unstubAllGlobals());

describe("browser crypto over LAN HTTP", () => {
  it("preserves native UUID generation and initializes only once", () => {
    vi.stubGlobal("crypto", webcrypto);
    const native = crypto.randomUUID;
    installRandomUuidFallback();
    installRandomUuidFallback();
    expect(crypto.randomUUID).toBe(native);
  });

  it("uses browser random bytes with UUID v4 version and variant bits", () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.set(Array.from({ length: 16 }, (_, index) => index));
      return bytes;
    });
    vi.stubGlobal("crypto", { getRandomValues });
    installRandomUuidFallback();
    const fallback = crypto.randomUUID;
    installRandomUuidFallback();
    expect(crypto.randomUUID).toBe(fallback);
    expect(crypto.randomUUID()).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f");
    expect(getRandomValues).toHaveBeenCalledTimes(1);
    expect(getRandomValues.mock.calls[0]?.[0]).toHaveLength(16);
  });

  it("fails explicitly if cryptographic randomness is unavailable", () => {
    vi.stubGlobal("crypto", undefined);
    expect(installRandomUuidFallback).toThrow(
      "Cryptographic random number generation",
    );
  });

  it.each(["", "abc", "机器人模型".repeat(100)])(
    "hashes files identically with and without Web Crypto: %s",
    async (content) => {
      const expected = createHash("sha256").update(content).digest("hex");
      vi.stubGlobal("crypto", webcrypto);
      expect(await sha256File(new Blob([content]))).toBe(expected);
      vi.stubGlobal("crypto", {
        getRandomValues: webcrypto.getRandomValues.bind(webcrypto),
      });
      expect(await sha256File(new Blob([content]))).toBe(expected);
    },
  );
});
