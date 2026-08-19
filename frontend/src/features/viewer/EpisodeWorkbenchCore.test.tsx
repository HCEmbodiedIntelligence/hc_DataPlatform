// @vitest-environment jsdom

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { createPlaybackClock } from './PlaybackClock';
import { EpisodeWorkbenchCore } from './EpisodeWorkbenchCore';
import type { ViewerTimelineTrack } from './EpisodeWorkbenchCore';

beforeAll(() => {
  if (!window.PointerEvent) {
    class TestPointerEvent extends MouseEvent {
      readonly pointerId: number;

      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 0;
      }
    }
    Object.defineProperty(window, 'PointerEvent', {
      configurable: true,
      value: TestPointerEvent,
    });
  }

  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
    configurable: true,
    value: () => undefined,
  });
});

afterEach(cleanup);

const tracks: readonly ViewerTimelineTrack[] = [
  {
    id: 'phase',
    label: '阶段',
    level: 0,
    segments: [
      {
        id: 'phase-1',
        label: '抓取零件',
        startNs: '2000000000',
        endNs: '6000000000',
        tone: 'phase',
      },
    ],
  },
  { id: 'action', label: '动作', level: 1, segments: [] },
];

function renderTimeline() {
  const clock = createPlaybackClock({ startNs: '0', endNs: '10000000000' });
  const onRangeSelect = vi.fn();
  render(
    <EpisodeWorkbenchCore
      episodeId="episode-1"
      datasetId="dataset-1"
      versionId="version-1"
      clock={clock}
      mode="annotate"
      streams={[]}
      timelineSelection={{
        startNs: '2000000000',
        endNs: '6000000000',
        label: '抓取零件',
      }}
      timelineTracks={tracks}
      onTimeRangeSelect={onRangeSelect}
    />,
  );
  return { clock, onRangeSelect };
}

describe('ClipTimeline', () => {
  it('creates a precise range by dragging across the filmstrip', () => {
    const { clock, onRangeSelect } = renderTimeline();
    const filmstrip = screen.getByRole('slider', { name: /播放位置/ });
    vi.spyOn(filmstrip, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 100,
      bottom: 76,
      width: 100,
      height: 76,
      toJSON: () => ({}),
    });

    fireEvent.pointerDown(filmstrip, { pointerId: 7, button: 0, clientX: 20 });
    fireEvent.pointerMove(filmstrip, { pointerId: 7, clientX: 60 });
    fireEvent.pointerUp(filmstrip, { pointerId: 7, clientX: 60 });

    expect(onRangeSelect).toHaveBeenLastCalledWith('2000000000', '6000000000');
    clock.dispose();
  });

  it('supports keyboard trimming, zooming, and nested track labels', () => {
    const { clock, onRangeSelect } = renderTimeline();

    fireEvent.keyDown(screen.getByRole('button', { name: /标注开始/ }), { key: 'ArrowRight' });
    expect(onRangeSelect).toHaveBeenLastCalledWith('2010000000', '6000000000');

    fireEvent.click(screen.getByRole('button', { name: '放大时间轴' }));
    expect(screen.getByRole('status', { name: '时间轴缩放' })).toHaveTextContent('2x');
    expect(screen.getAllByText('阶段')).toHaveLength(2);
    expect(screen.getAllByText('动作')).toHaveLength(2);
    clock.dispose();
  });
});
