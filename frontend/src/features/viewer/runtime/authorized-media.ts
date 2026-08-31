import { createDomainError } from "../../../shared/api/domain-error";

export type MediaFatalHandler = (error: Error) => void;

function mediaPlaybackError(message: string): Error {
  return createDomainError({
    code: "NETWORK_ERROR",
    message,
    fieldErrors: [],
    operationErrors: [{ code: "MP4_PLAYBACK_FAILED", message }],
    blockedReasons: [],
    requestId: null,
    retryable: true,
    httpStatus: null,
  });
}

export async function attachAuthorizedMedia(
  video: HTMLVideoElement,
  url: string,
  onFatal: MediaFatalHandler,
): Promise<() => void> {
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
