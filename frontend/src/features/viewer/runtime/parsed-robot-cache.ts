import type { BufferGeometry, Material, Object3D } from 'three';
import { useShellStore } from '../../../shared/scope/shell-store';

interface Entry<T> { template: T; bytes: number; users: number; retired: boolean }

/** Share parsed geometry, with independent URDF joints/poses for every viewer. */
export class ParsedRobotCache<T extends Object3D> {
  private entries = new Map<string, Entry<T>>();
  constructor(private dispose: (model: T) => void, private limit = 192 * 1024 * 1024) {}

  acquire(key: string) {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    this.entries.delete(key);
    this.entries.set(key, entry);
    return this.lease(entry);
  }

  put(key: string, template: T) {
    const existing = this.acquire(key);
    if (existing) { this.dispose(template); return existing; }
    const seen = new Set<unknown>();
    let bytes = 0;
    template.traverse(object => {
      const mesh = object as Object3D & { geometry?: BufferGeometry; material?: Material | Material[] };
      if (mesh.geometry && !seen.has(mesh.geometry)) {
        seen.add(mesh.geometry);
        for (const attribute of Object.values(mesh.geometry.attributes)) bytes += attribute.array.byteLength;
        bytes += mesh.geometry.index?.array.byteLength ?? 0;
      }
    });
    const entry = { template, bytes, users: 0, retired: bytes > this.limit };
    if (!entry.retired) this.entries.set(key, entry);
    const lease = this.lease(entry);
    let retained = [...this.entries.values()].reduce((n, item) => n + item.bytes, 0);
    for (const [oldKey, old] of this.entries) {
      if (retained <= this.limit) break;
      this.entries.delete(oldKey); retained -= old.bytes;
      this.retire(old);
    }
    return lease;
  }

  clear() {
    for (const entry of this.entries.values()) this.retire(entry);
    this.entries.clear();
  }

  private retire(entry: Entry<T>) {
    entry.retired = true;
    if (!entry.users) this.dispose(entry.template);
  }

  private lease(entry: Entry<T>) {
    entry.users++;
    const robot = entry.template.clone(true) as T;
    let released = false;
    return { robot, release: () => {
      if (released) return;
      released = true;
      entry.users--;
      if (entry.retired && !entry.users) this.dispose(entry.template);
    } };
  }
}

export function clearOnAuthorizationChange(cache: { clear(): void }) {
  return useShellStore.subscribe((state, previous) => {
    if (state.sessionToken !== previous.sessionToken || state.scopeKey !== previous.scopeKey ||
        state.capabilityRevision !== previous.capabilityRevision) cache.clear();
  });
}
