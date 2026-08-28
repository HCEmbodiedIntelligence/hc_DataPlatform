import { createDomainError } from "../../../shared/api/domain-error";

export type MediaFatalHandler = (error: Error) => void;

function isHlsPlaylist(url: string): boolean {
  try {
    return new URL(url, window.location.href).pathname.endsWith(".m3u8");
  } catch {
    return url.split("?", 1)[0]?.endsWith(".m3u8") ?? false;
  }
}

function mediaPlaybackError(message: string): Error {
  return createDomainError({
    code: "NETWORK_ERROR",
    message,
    fieldErrors: [],
    operationErrors: [{ code: "HLS_PLAYBACK_FAILED", message }],
    blockedReasons: [],
    requestId: null,
    retryable: true,
    httpStatus: null,
  });
}

function attachNativeMedia(
  video: HTMLVideoElement,
  url: string,
  onFatal: MediaFatalHandler,
): () => void {
  let disposed = false;
  const onError = () => {
    if (!disposed) onFatal(mediaPlaybackError("媒体播放请求失败。"));
  };
  video.addEventListener("error", onError);
  video.src = url;
  video.load();
  return () => {
    disposed = true;
    video.removeEventListener("error", onError);
  };
}

/**
 * Attach a browser-playable source without pulling HLS.js into the initial viewer bundle.
 * Native HLS is used where available; Chromium/Firefox lazily load the MSE decoder only
 * after a visible video panel receives an HLS descriptor.
 */
export async function attachAuthorizedMedia(
  video: HTMLVideoElement,
  url: string,
  onFatal: MediaFatalHandler,
): Promise<() => void> {
  if (!isHlsPlaylist(url)) return attachNativeMedia(video, url, onFatal);

  const { default: Hls } = await import("hls.js");
  if (Hls.isSupported()) {
    const hls = new Hls({
      // These previews are short, synchronized VOD clips. Keep one complete
      // working window available so replay does not re-download three camera
      // streams at once after the browser evicts ManagedMediaSource data.
      preferManagedMediaSource: false,
      maxBufferLength: 20,
      maxMaxBufferLength: 20,
      backBufferLength: 20,
      maxBufferSize: 32 * 1024 * 1024,
      startFragPrefetch: true,
    });
    let disposed = false;
    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (data.fatal && !disposed) {
        onFatal(mediaPlaybackError("HLS 媒体流不可用。"));
      }
    });
    hls.loadSource(url);
    hls.attachMedia(video);
    return () => {
      disposed = true;
      hls.destroy();
    };
  }

  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    return attachNativeMedia(video, url, onFatal);
  }
  throw createDomainError({
    code: "CONTRACT_MISMATCH",
    message: "此浏览器不支持 HLS 预览播放。",
    fieldErrors: [],
    operationErrors: [
      {
        code: "HLS_BROWSER_UNSUPPORTED",
        message: "此浏览器不支持 HLS 预览播放。",
      },
    ],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}
