import { createSessionScopeLease } from '../../shared/api/session-scope-lease';
import { getShellState, useShellStore } from '../../shared/scope/shell-store';
import type { Scope } from '../../shared/scope/model';
import { makeScopeKey } from '../../shared/scope/model';

const databaseName = 'hc-private-model-preview-v1';
const maxBytes = 192 * 1024 * 1024;
let generation = 0;
let database: Promise<IDBDatabase | null> | undefined;
function openDatabase() {
  return database ??= new Promise<IDBDatabase | null>(resolve => {
    if (!globalThis.indexedDB) { resolve(null); return; }
    let request: IDBOpenDBRequest;
    try { request = indexedDB.open(databaseName, 2); } catch { resolve(null); return; }
    request.onupgradeneeded = () => {
      for (const name of ['assets', 'partials'])
        if (!request.result.objectStoreNames.contains(name)) request.result.createObjectStore(name, { keyPath: 'key' });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = request.onblocked = () => resolve(null);
  });
}
async function transaction<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>, name = 'assets') {
  const db = await openDatabase();
  if (!db) return undefined;
  return new Promise<T | undefined>(resolve => {
    try {
      const tx = db.transaction(name, mode);
      const request = action(tx.objectStore(name));
      tx.oncomplete = () => resolve(request.result);
      tx.onerror = tx.onabort = () => resolve(undefined);
    } catch { resolve(undefined); }
  });
}
useShellStore.subscribe((state, previous) => {
  if (state.sessionToken !== previous.sessionToken || state.scopeKey !== previous.scopeKey ||
      (previous.capabilityRevision !== null && state.capabilityRevision !== previous.capabilityRevision)) {
    generation++;
    for (const name of ['assets', 'partials']) void transaction('readwrite', store => store.clear(), name);
  }
});
interface RecordEntry { key: string; blob: Blob; used: number; size: number }
async function save(entry: RecordEntry, epoch: number, name = 'assets') {
  if (generation !== epoch) return;
  await transaction('readwrite', store => {
    if (generation !== epoch) throw new Error('Cache identity changed');
    return store.put(entry);
  }, name);
}
async function prune(epoch: number) {
  if (generation !== epoch) return;
  const entries = (await Promise.all(['assets', 'partials'].map(async name =>
    (await transaction<RecordEntry[]>('readonly', store => store.getAll(), name) ?? []).map(e => ({ ...e, name }))
  ))).flat();
  let total = entries.reduce((n, e) => n + e.size, 0);
  for (const entry of entries.sort((a,b) => a.used-b.used)) {
    if (total <= maxBytes || generation !== epoch) break;
    await transaction('readwrite', store => store.delete(entry.key), entry.name);
    total -= entry.size;
  }
}
export class ModelAssetLoadError extends Error {
  constructor(message: string) { super(message); this.name = 'ModelAssetLoadError'; }
}
async function digest(bytes: ArrayBuffer): Promise<string> {
  if (globalThis.crypto?.subtle) {
    const result = await crypto.subtle.digest('SHA-256', bytes);
    return [...new Uint8Array(result)].map(v => v.toString(16).padStart(2,'0')).join('');
  }
  const { sha256 } = await import('@noble/hashes/sha2.js');
  return [...sha256(new Uint8Array(bytes))].map(v => v.toString(16).padStart(2,'0')).join('');
}
export interface AuthorizedModelFile {
  asset: { asset_id: string; relative_path: string; role: string };
  grant: { download_url: string; sha256: string; expires_at: string; media_type: string };
  sizeBytes: number;
}
export type ModelLoadProgress = (loaded: number, total: number, bytes?: number, totalBytes?: number) => void;
export function authorizedModelCacheKey(files: readonly AuthorizedModelFile[], scope: Scope): string {
  return JSON.stringify([generation, getShellState().principal?.actorId, makeScopeKey(scope),
    files.map(file => [file.asset.relative_path, file.grant.sha256, file.sizeBytes]).sort()]);
}

// The application owns transfers. Route unmount aborts its wait, not the shared
// download; logout/scope revocation still cancel every transfer via the lease.
interface Transfer { promise: Promise<Blob>; loaded: number; listeners: Set<(bytes: number) => void> }
const transfers = new Map<string, Transfer>();
let active = 0;
const slots: Array<() => void> = [];
async function slot() {
  if (active >= 4) await new Promise<void>(resolve => slots.push(resolve));
  else active++;
  return () => { const next = slots.shift(); if (next) next(); else active--; };
}
function keyFor(file: AuthorizedModelFile, scope: Scope) {
  return JSON.stringify([getShellState().principal?.actorId, makeScopeKey(scope)]) + '/' + file.grant.sha256;
}

async function download(file: AuthorizedModelFile, scope: Scope, key: string, epoch: number, progress: (bytes: number) => void) {
  const idle = new AbortController();
  const lease = createSessionScopeLease(scope, idle.signal);
  let timer: ReturnType<typeof setTimeout> | undefined;
  const resetTimer = () => { clearTimeout(timer); timer = setTimeout(() => idle.abort('download-stalled'), 30_000); };
  const check = () => { lease.signal.throwIfAborted(); if (generation !== epoch) throw new DOMException('Model authorization changed', 'AbortError'); };
  let free: (() => void) | undefined;
  try {
    check();
    if (Date.parse(file.grant.expires_at) <= Date.now()) throw new ModelAssetLoadError('模型授权已过期，请重试。');
    const cached = await transaction<RecordEntry>('readonly', store => store.get(key));
    check();
    if (cached?.blob instanceof Blob && cached.blob.size === file.sizeBytes && await digest(await cached.blob.arrayBuffer()) === file.grant.sha256) {
      progress(file.sizeBytes); return cached.blob;
    }
    const partial = await transaction<RecordEntry>('readonly', store => store.get(key), 'partials');
    let offset = partial?.blob instanceof Blob && partial.blob.size < file.sizeBytes ? partial.blob.size : 0;
    let parts: BlobPart[] = offset ? [partial!.blob] : [];
    free = await slot(); check(); resetTimer();
    const response = await fetch(file.grant.download_url, { signal: lease.signal, cache: 'no-store', credentials: 'same-origin',
      ...(offset ? { headers: { Range: `bytes=${offset}-` } } : {}) });
    if (!response.ok) throw new ModelAssetLoadError(`模型下载失败（HTTP ${response.status}），请重试。`);
    if (response.status === 206) {
      const expected = `bytes ${offset}-${file.sizeBytes-1}/${file.sizeBytes}`;
      if (response.headers.get('Content-Range') !== expected) throw new ModelAssetLoadError('模型续传范围不匹配，请重试。');
    } else { offset = 0; parts = []; }
    progress(offset);
    let lastSaved = offset;
    const reader = response.body?.getReader();
    if (!reader) throw new ModelAssetLoadError('模型下载响应为空，请重试。');
    try {
      while (true) {
        const {done, value} = await reader.read(); check();
        if (done) break;
        resetTimer();
        parts.push(new Uint8Array(value).buffer); offset += value.byteLength;
        if (offset > file.sizeBytes) throw new ModelAssetLoadError('模型文件大小不匹配，请重试。');
        progress(offset);
        if (offset-lastSaved >= 256*1024) {
          await save({key,blob:new Blob(parts),size:offset,used:Date.now()},epoch,'partials'); lastSaved=offset;
        }
      }
    } catch (error) {
      if (generation === epoch && offset > 0 && offset < file.sizeBytes)
        await save({key,blob:new Blob(parts),size:offset,used:Date.now()},epoch,'partials');
      throw error;
    } finally { reader.releaseLock(); }
    const blob = new Blob(parts,{type:file.grant.media_type});
    check();
    if (blob.size !== file.sizeBytes || await digest(await blob.arrayBuffer()) !== file.grant.sha256) {
      await transaction('readwrite',store=>store.delete(key),'partials');
      throw new ModelAssetLoadError('模型文件校验失败，请重试。');
    }
    await save({key,blob,size:blob.size,used:Date.now()},epoch);
    await transaction('readwrite',store=>store.delete(key),'partials');
    check();return blob;
  } catch (error) {
    void prune(epoch);
    if (generation !== epoch || (!idle.signal.aborted && lease.signal.aborted)) throw error;
    if (error instanceof ModelAssetLoadError) throw error;
    throw new ModelAssetLoadError(idle.signal.aborted ? '模型传输暂时中断，已保存进度，请重试。' : '模型连接失败，已保存进度，请重试。');
  } finally {clearTimeout(timer);free?.();lease.release();}
}

function sharedFile(file: AuthorizedModelFile, scope: Scope, progress: (bytes:number)=>void) {
  const key = keyFor(file,scope), identity = generation+'/'+key;
  let transfer = transfers.get(identity);
  if (!transfer) {
    const listeners = new Set<(bytes:number)=>void>();
    const entry: Transfer = {loaded:0,listeners,promise:undefined!};
    entry.promise = download(file,scope,key,generation,bytes=>{entry.loaded=bytes;for(const listener of listeners)listener(bytes);});
    transfers.set(identity,entry);transfer=entry;
    void entry.promise.finally(()=>transfers.delete(identity)).catch(()=>undefined);
  }
  transfer.listeners.add(progress);progress(transfer.loaded);
  return {promise:transfer.promise,release:()=>transfer.listeners.delete(progress)};
}

function waitFor<T>(promise:Promise<T>, signal:AbortSignal):Promise<T> {
  signal.throwIfAborted();
  return new Promise((resolve,reject)=>{
    const aborted=()=>reject(new DOMException('Model wait aborted','AbortError'));
    signal.addEventListener('abort',aborted,{once:true});
    promise.then(resolve,reject).finally(()=>signal.removeEventListener('abort',aborted));
  });
}

async function loadFiles(files:readonly AuthorizedModelFile[],scope:Scope,signal:AbortSignal,progress?:ModelLoadProgress) {
  const lease=createSessionScopeLease(scope,signal),epoch=generation;
  if (lease.signal.aborted) { lease.release(); lease.signal.throwIfAborted(); }
  const bytes=files.map(()=>0),totalBytes=files.reduce((n,f)=>n+f.sizeBytes,0);
  let loaded=0;
  const pending=files.map((file,index)=>sharedFile(file,scope,size=>{bytes[index]=size;if(!lease.signal.aborted)progress?.(loaded,files.length,bytes.reduce((a,b)=>a+b,0),totalBytes);}));
  try {
    const blobs=await waitFor(Promise.all(pending.map(async item=>{const blob=await item.promise;loaded++;if(!lease.signal.aborted)progress?.(loaded,files.length,bytes.reduce((a,b)=>a+b,0),totalBytes);return blob;})),lease.signal);
    lease.signal.throwIfAborted();await prune(epoch);lease.signal.throwIfAborted();return blobs;
  } finally {pending.forEach(p=>p.release());lease.release();}
}

export async function preloadAuthorizedModel(files:readonly AuthorizedModelFile[],scope:Scope,signal:AbortSignal) {
  await loadFiles(files,scope,signal);
}

/** All model viewers share transfers, verified bytes and parsed geometry. */
export function authorizedModelSceneAssets(files: readonly AuthorizedModelFile[], scope: Scope, progress?: ModelLoadProgress) {
  const urdf = files.find(file => file.asset.role === 'URDF');
  if (!urdf) throw new ModelAssetLoadError('模型缺少 URDF。');
  return {
    urdfUrl: urdf.grant.download_url,
    urdfPath: urdf.asset.relative_path,
    assetUrls: Object.fromEntries(files.filter(file => file !== urdf).map(file => [file.asset.relative_path, file.grant.download_url])),
    parsedCacheKey: authorizedModelCacheKey(files, scope),
    prepare: (signal: AbortSignal) => prepareAuthorizedModel(files, scope, signal, progress),
  };
}
/** Fresh scoped grants are mandatory even for cached data; URLs are never persisted. */
export async function prepareAuthorizedModel(files:readonly AuthorizedModelFile[],scope:Scope,signal:AbortSignal,progress?:ModelLoadProgress) {
  const blobs=await loadFiles(files,scope,signal,progress);
  signal.throwIfAborted();
  const urls=Object.fromEntries(files.map((file,index)=>[file.asset.relative_path,URL.createObjectURL(blobs[index]!)]));
  const release=()=>Object.values(urls).forEach(url=>URL.revokeObjectURL(url));
  const urdf=files.find(file=>file.asset.role==='URDF');
  if (!urdf) {release();throw new ModelAssetLoadError('固定模型缺少 URDF。');}
  return {urdfUrl:urls[urdf.asset.relative_path]!,assetUrls:urls,
    glbUrls:files.filter(file=>file.grant.media_type==='model/gltf-binary').map(file=>urls[file.asset.relative_path]!),release};
}
