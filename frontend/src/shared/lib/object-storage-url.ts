/** Keep local signed reads on the gateway while preserving the signing Host. */
export function localObjectStorageUrl(raw: string): string {
  if (
    !import.meta.env.DEV ||
    typeof window === "undefined" ||
    !["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname)
  )
    return raw;
  const url = new URL(raw, window.location.href);
  if (
    url.origin === window.location.origin ||
    url.origin !== import.meta.env.VITE_ROBOT_PLATFORM_BASE_URL ||
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    !/^[A-Za-z0-9.-]+(?::[0-9]+)?$/u.test(url.host) ||
    !url.pathname.startsWith("/hc-data-local/") ||
    !url.searchParams.has("X-Amz-Signature")
  )
    return raw;
  // Do not decode or rebuild the query: filenames and signatures are encoded.
  return `/_recording_media/${url.host}${url.pathname}${url.search}`;
}
