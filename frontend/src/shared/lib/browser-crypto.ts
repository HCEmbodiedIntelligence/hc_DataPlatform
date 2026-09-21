/** Keep cryptographic UUIDs available when the app is opened over LAN HTTP. */
export function installRandomUuidFallback(): void {
  const browserCrypto = globalThis.crypto;
  if (typeof browserCrypto?.randomUUID === "function") return;
  if (typeof browserCrypto?.getRandomValues !== "function") {
    throw new Error("Cryptographic random number generation is unavailable");
  }

  browserCrypto.randomUUID = () => {
    const bytes = browserCrypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6]! & 0x0f) | 0x40;
    bytes[8] = (bytes[8]! & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };
}

export async function sha256File(file: Blob): Promise<string> {
  const data = await file.arrayBuffer();
  const subtle = globalThis.crypto?.subtle;
  const digest = subtle
    ? new Uint8Array(await subtle.digest("SHA-256", data))
    : (await import("@noble/hashes/sha2.js")).sha256(new Uint8Array(data));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
}
