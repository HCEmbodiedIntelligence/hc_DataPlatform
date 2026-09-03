export interface DisposableLike { dispose(): void }

function safe(run: () => void): void {
  try { run(); } catch { /* disposal is best effort and must continue */ }
}

export class ViewerResourceRegistry implements DisposableLike {
  private readonly disposers: Array<() => void> = [];
  private disposed = false;

  add(disposer: (() => void) | DisposableLike | null | undefined): void {
    if (!disposer) return;
    const fn = typeof disposer === 'function' ? disposer : () => disposer.dispose();
    if (this.disposed) safe(fn);
    else this.disposers.push(fn);
  }

  trackObjectUrl(url: string): string {
    if (url.startsWith('blob:') && typeof URL.revokeObjectURL === 'function') {
      this.add(() => URL.revokeObjectURL(url));
    }
    return url;
  }

  trackMedia(element: HTMLMediaElement): void {
    this.add(() => {
      element.pause();
      element.removeAttribute('src');
      element.load();
    });
  }

  trackWorker(worker: Worker): void {
    this.add(() => worker.terminate());
  }

  trackAnimationFrame(id: number): void {
    this.add(() => cancelAnimationFrame(id));
  }

  trackGpuResource(resource: { dispose?: () => void }): void {
    if (typeof resource.dispose === 'function') this.add(() => resource.dispose?.());
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (let index = this.disposers.length - 1; index >= 0; index -= 1) {
      const disposer = this.disposers[index];
      if (disposer) safe(disposer);
    }
    this.disposers.length = 0;
  }
}
