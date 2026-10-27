import { createHash } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { prepareAuthorizedModel, authorizedModelSceneAssets } from './authorized-model-cache';
import { useShellStore } from '../../shared/scope/shell-store';

const scope = {organizationId:'org',projectId:'project',regionCode:'region'};
const payload = '<robot/>';
const file = {asset:{asset_id:'urdf',relative_path:'model.urdf',role:'URDF'},sizeBytes:payload.length,
 grant:{download_url:'https://example.test/signed',sha256:createHash('sha256').update(payload).digest('hex'),expires_at:'2099-01-01T00:00:00Z',media_type:'application/xml'}};
beforeEach(()=>{
 const store=useShellStore.getState();
 store.setSession({actorId:'reader',displayName:'Reader',roleIds:[]},'session');
 store.setSessionScopes([{...scope,regionCodes:['region'],projectWide:false,capabilities:['robot_model.read','dataset_version.read']}],1);
 store.setScope(scope);
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(payload)));
});
afterEach(()=>{useShellStore.getState().setSession(null,null);vi.unstubAllGlobals();vi.restoreAllMocks();});
it('reuses geometry identity across data viewers with fresh grants and equivalent scopes', () => {
 const annotation = authorizedModelSceneAssets([file], scope);
 const recording = authorizedModelSceneAssets([{...file, grant:{...file.grant, download_url:'https://example.test/new-grant'}}], {
  regionCode:scope.regionCode,projectId:scope.projectId,organizationId:scope.organizationId,
 });
 expect(recording.parsedCacheKey).toBe(annotation.parsedCacheKey);
 expect(authorizedModelSceneAssets([{...file,grant:{...file.grant,sha256:'b'.repeat(64)}}],scope).parsedCacheKey).not.toBe(annotation.parsedCacheKey);
});
it('checks exact content and releases temporary object URLs',async()=>{
 const revoke=vi.spyOn(URL,'revokeObjectURL');
 const result=await prepareAuthorizedModel([file],scope,new AbortController().signal);
 expect(result.urdfUrl).toMatch(/^blob:/);
 result.release();expect(revoke).toHaveBeenCalledWith(result.urdfUrl);
});
it('rejects altered content and expired grants before rendering',async()=>{
 vi.mocked(fetch).mockResolvedValueOnce(new Response('corrupt'));
 await expect(prepareAuthorizedModel([file],scope,new AbortController().signal)).rejects.toThrow('校验失败');
 await expect(prepareAuthorizedModel([{...file,grant:{...file.grant,expires_at:'2000-01-01T00:00:00Z'}}],scope,new AbortController().signal)).rejects.toThrow('已过期');
});
it('does not hand out a model after logout during a download',async()=>{
 let finish!: (response:Response)=>void;
 vi.mocked(fetch).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 const pending=prepareAuthorizedModel([file],scope,new AbortController().signal);
 const check=expect(pending).rejects.toBeDefined();
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalled());
 useShellStore.getState().setSession(null,null);
 finish(new Response(payload));
 await check;
});
it('keeps one shared download alive across route cancellation',async()=>{
 let finish!: (response:Response)=>void;
 vi.mocked(fetch).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 const firstController=new AbortController();
 const first=prepareAuthorizedModel([file],scope,firstController.signal);
 const rejected=expect(first).rejects.toMatchObject({name:'AbortError'});
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));
 firstController.abort();await rejected;
 const second=prepareAuthorizedModel([file],scope,new AbortController().signal);
 finish(new Response(payload));
 const result=await second;
 expect(fetch).toHaveBeenCalledTimes(1);result.release();
});
