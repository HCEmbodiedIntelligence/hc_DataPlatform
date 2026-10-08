import { useEffect, useState } from "react";
import { useClockText } from "../PlaybackClock";
import type { ViewerRendererContext } from "../renderer-contract";

/** Extension test host consumes the same source, clock and resource ownership ports. */
export function Component({
  stream,
  clock,
  signal,
  resources,
  reportError,
}: ViewerRendererContext) {
  const time = useClockText(clock);
  const [caption, setCaption] = useState("");
  useEffect(() => {
    stream.windowSource
      ?.loadWindow(
        { startNs: stream.startNs, endNs: stream.endNs, lod: 1 },
        signal,
      )
      .then((payload) => {
        resources.add(payload.dispose);
        if (!signal.aborted) setCaption(payload.events?.[0]?.label ?? "");
      })
      .catch(reportError);
  }, [stream, signal, resources, reportError]);
  return (
    <output aria-label="扩展字幕">
      {caption} @ {time}
    </output>
  );
}
