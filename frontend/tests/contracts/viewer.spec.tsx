import { act, render, screen, waitFor } from '@testing-library/react';
import type { JSX } from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  configureRobotSceneRuntime,
  createPlaybackClock,
  EpisodeWorkbenchCore,
  resolveViewerComposition,
  retrySignedResourceOnce,
  RobotSceneCore,
  useClockText,
  ViewerResourceRegistry,
} from '../../src/features/viewer';
import type { RobotSceneManifest, RobotSceneRuntime } from '../../src/features/viewer/RobotSceneCore';
import type { StreamDescriptor } from '../../src/features/viewer';

const stream = (id: string, modality: string, axes?: number, shape = axes): StreamDescriptor => ({
  id,
  canonicalPath: `fixture/${id}`,
  displayName: id,
  modality,
  schema: {
    id: `schema:${id}`,
    version: '1',
    ...(shape === undefined ? {} : { shape: [shape] }),
    ...(axes === undefined ? {} : { axes: Array.from({ length: axes }, (_, index) => ({ axisId: `axis-${index}`, sourceName: `axis-${index}`, displayName: `Axis ${index}`, sampleIndex: index, unit: 'rad', mappingStatus: 'mapped' as const })) }),
  },
  startNs: '0',
  endNs: '10000000000',
  availability: 'ready',
});

describe('Viewer core contract', () => {
  it('resolves dynamic 6/7/14 axis layouts without phantom panels and fails mismatches locally', () => {
    const seven = resolveViewerComposition([stream('rgb', 'rgb'), stream('joints', 'joint_state', 7)]);
    expect(seven.panels.map((panel) => panel.kind)).toEqual(['video']);
    expect(seven.jointGroups[0]?.axes).toHaveLength(7);
    const sixPointcloud = resolveViewerComposition([stream('rgb', 'rgb'), stream('points', 'pointcloud'), stream('joints', 'joint_state', 6)]);
    expect(sixPointcloud.panels.map((panel) => panel.kind)).toEqual(['video', 'pointcloud-preview']);
    expect(sixPointcloud.jointGroups[0]?.axes).toHaveLength(6);
    expect(resolveViewerComposition([stream('joints', 'joint_state', 14)]).jointGroups[0]?.axes).toHaveLength(14);
    const mismatch = resolveViewerComposition([stream('rgb', 'rgb'), stream('bad', 'joint_state', 6, 7)]);
    expect(mismatch.panels).toHaveLength(1);
    expect(mismatch.jointGroups).toHaveLength(0);
    expect(mismatch.diagnostics[0]?.code).toBe('JOINT_AXIS_CONTRACT_MISMATCH');
    expect(resolveViewerComposition([stream('future', 'neural-field')]).panels[0]?.kind).toBe('unsupported');
  });

  it('keeps high-frequency ticks outside React and throttles text to at most 10Hz', async () => {
    vi.useFakeTimers();
    const clock = createPlaybackClock({ startNs: '0', endNs: '10000000000' });
    let renders = 0;
    function Readout(): JSX.Element { renders += 1; return <span>{useClockText(clock)}</span>; }
    render(<Readout />);
    act(() => { for (let index = 0; index < 60; index += 1) clock.seek(String(index * 1_000_000)); });
    expect(renders).toBe(1);
    await act(() => {
      vi.advanceTimersByTime(100);
      return Promise.resolve();
    });
    expect(renders).toBeLessThanOrEqual(2);
    clock.dispose();
    vi.useRealTimers();
  });

  it('releases media, workers, object URLs, RAF and GPU disposables on registry disposal', () => {
    const registry = new ViewerResourceRegistry();
    const pause = vi.fn();
    const load = vi.fn();
    const removeAttribute = vi.fn();
    const terminate = vi.fn();
    const gpuDispose = vi.fn();
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    registry.trackMedia({ pause, load, removeAttribute } as unknown as HTMLMediaElement);
    registry.trackWorker({ terminate } as unknown as Worker);
    registry.trackObjectUrl('blob:fixture');
    registry.trackGpuResource({ dispose: gpuDispose });
    registry.dispose();
    expect(pause).toHaveBeenCalledOnce();
    expect(removeAttribute).toHaveBeenCalledWith('src');
    expect(load).toHaveBeenCalledOnce();
    expect(terminate).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith('blob:fixture');
    expect(gpuDispose).toHaveBeenCalledOnce();
  });

  it('refreshes an expired signed resource only once', async () => {
    const initial = vi.fn().mockRejectedValue({ code: 'SIGNED_URL_EXPIRED', httpStatus: 410 });
    const refresh = vi.fn().mockResolvedValue('fresh');
    await expect(retrySignedResourceOnce(initial, refresh)).resolves.toBe('fresh');
    expect(initial).toHaveBeenCalledOnce();
    expect(refresh).toHaveBeenCalledOnce();
    await expect(retrySignedResourceOnce(initial, vi.fn().mockRejectedValue(new Error('still expired')))).rejects.toThrow('still expired');
  });

  it('mounts panels against one clock and disposes window payloads on unmount', async () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    const payloadDispose = vi.fn();
    const loadWindow = vi.fn().mockResolvedValue({ generation: 1, timestampsNs: [], dispose: payloadDispose });
    const clock = createPlaybackClock({ startNs: '0', endNs: '10000000000' });
    const source = { ...stream('curve', 'force'), windowSource: { loadWindow } };
    const view = render(<EpisodeWorkbenchCore episodeId="episode" datasetId="dataset" versionId="version" clock={clock} mode="annotate" streams={[source]} />);
    await waitFor(() => expect(loadWindow).toHaveBeenCalledOnce());
    view.unmount();
    expect(payloadDispose).toHaveBeenCalledOnce();
    clock.dispose();
    getContext.mockRestore();
  });

  it('blocks incompatible 3D facts and attempts WebGL recovery once without affecting other panels', async () => {
    const canvas = document.createElement('canvas');
    const dispose = vi.fn();
    const restoreContext = vi.fn().mockResolvedValue(false);
    const runtime: RobotSceneRuntime = { canvas, applyTime: vi.fn(), restoreContext, dispose };
    const manifest: RobotSceneManifest = { modelId: 'model', modelVersion: '1', requiredJoints: ['joint_a'] };
    const restoreLoader = configureRobotSceneRuntime(vi.fn().mockResolvedValue({ manifest, runtime }));
    const onLost = vi.fn();
    const mapping = { joint_a: 'joint_a' };
    const view = render(<RobotSceneCore modelRef={{ modelId: 'model', modelVersion: '1' }} jointMapping={mapping} onContextLost={onLost} />);
    await waitFor(() => expect(screen.getByLabelText('机器人 3D 场景')).toHaveAttribute('data-status', 'ready'));
    act(() => { canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true })); });
    await waitFor(() => expect(restoreContext).toHaveBeenCalledOnce());
    act(() => { canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true })); });
    expect(restoreContext).toHaveBeenCalledOnce();
    expect(onLost).toHaveBeenCalledWith(false);
    view.unmount();
    expect(dispose).toHaveBeenCalledOnce();
    restoreLoader();
  });

  it('notifies and refuses a model version mismatch before displaying the scene', async () => {
    const canvas = document.createElement('canvas');
    const dispose = vi.fn();
    const restoreLoader = configureRobotSceneRuntime(vi.fn().mockResolvedValue({ manifest: { modelId: 'model', modelVersion: '2', requiredJoints: [] }, runtime: { canvas, applyTime: vi.fn(), restoreContext: vi.fn(), dispose } }));
    const onIncompatible = vi.fn();
    render(<RobotSceneCore modelRef={{ modelId: 'model', modelVersion: '1' }} jointMapping={{}} onIncompatible={onIncompatible} />);
    await waitFor(() => expect(onIncompatible).toHaveBeenCalledWith('MODEL_VERSION'));
    expect(dispose).toHaveBeenCalledOnce();
    restoreLoader();
  });
});
