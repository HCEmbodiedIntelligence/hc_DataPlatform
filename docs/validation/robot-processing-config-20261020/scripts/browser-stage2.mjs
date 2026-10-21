import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const root='/home/hc_op/workspace/reports/robot-processing-config-20261020';
const fixture=JSON.parse(await readFile(root+'/browser-fixture.json','utf8'));
const origin='http://127.0.0.1:5180';let sid;const result={checks:[],screenshots:[]};
async function wd(path, body){const response=await fetch('http://127.0.0.1:4446'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(45000)});const data=await response.json();if(!response.ok)throw Error(JSON.stringify(data.value));return data.value;}
async function js(script,args=[]){return wd(`/session/${sid}/execute/sync`,{script,args});}
async function pause(){await new Promise(r=>setTimeout(r,250));}
async function until(script,label){for(let i=0;i<80;i++){if(await js(script))return;await pause();}throw Error('Timed out: '+label);}
async function goto(path){await wd(`/session/${sid}/url`,{url:origin+path});}
async function clickElement(el){assert.ok(el,'element exists'); const id=el['element-6066-11e4-a52e-4f735466cecf'];await wd(`/session/${sid}/element/${id}/click`,{});}
async function click(css){await clickElement(await js('return document.querySelector(arguments[0])',[css]));}
async function textClick(label,selector='button'){const script=`return [...document.querySelectorAll(arguments[1])].find(e=>e.getBoundingClientRect().width>0 && (e.getAttribute('aria-label')||e.textContent).replace(/\\s/g,'')===arguments[0].replace(/\\s/g,''))`;let el;for(let i=0;i<40;i++){el=await js(script,[label,selector]);if(el)break;await pause();}await clickElement(el);}
async function fill(css,value){const el=await js('return document.querySelector(arguments[0])',[css]);const id=el['element-6066-11e4-a52e-4f735466cecf'];await wd(`/session/${sid}/element/${id}/clear`,{});await wd(`/session/${sid}/element/${id}/value`,{text:value});}
async function shot(name){await js(`document.querySelectorAll('textarea[data-sensitive],textarea[aria-label="上传 Token"],textarea[aria-label="机器人平台凭据"]').forEach(e=>e.style.visibility='hidden')`);await writeFile(`${root}/${name}.png`,Buffer.from(await wd(`/session/${sid}/screenshot`),'base64'));}
async function admin(path,method='GET',body){return js(`const s=JSON.parse(sessionStorage.getItem('hc-platform-current-session'));return fetch('/api/v1'+arguments[0],{method:arguments[1],headers:{Authorization:'Bearer '+s.sessionToken,'X-Organization-Id':s.scope.organizationId,'X-Region-Code':s.scope.regionCode,'Content-Type':'application/json'},...(arguments[2]===null?{}:{body:JSON.stringify(arguments[2])})}).then(async r=>({status:r.status,data:r.status===204?null:await r.json()}))`,[path,method,body??null]);}
try {
const started=await wd('/session',{capabilities:{alwaysMatch:{browserName:'firefox',pageLoadStrategy:'eager','moz:firefoxOptions':{args:['-headless'],prefs:{'network.proxy.type':0}},timeouts:{pageLoad:30000,script:30000}}}});sid=started.sessionId;
await wd(`/session/${sid}/window/rect`,{width:1440,height:1120});
await goto('/');
const login=await js(`return fetch('/api/v1/auth/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'config-browser-admin',password:'ConfigTestOnly!2026'})}).then(async r=>({status:r.status,data:await r.json()}))`);
assert.equal(login.status,201,JSON.stringify(login));
await js(`sessionStorage.setItem('hc-platform-current-session',JSON.stringify({version:1,principal:{actorId:arguments[0].principal.principal_id,displayName:'Config test',roleIds:[]},sessionToken:arguments[0].access_token,scope:arguments[1]}))`,[login.data,{organizationId:fixture.organizationId,projectId:fixture.projectId,regionCode:fixture.regionCode}]);
await goto('/ingest/uploads/records');
await until(`return document.body.innerText.includes('MCAP_REQUIRED_RGB_MISSING')`,'failed MCAP record');
await textClick('查看原始通道');
await until(`return document.body.innerText.includes('message_count')`,'actual channel inventory');
await shot('05-original-channels');result.checks.push('Original channel inventory displays zero image counts');
await textClick('前往机器人配置','a');
await until(`return !!document.querySelector('[aria-label="方案名称"]')`,'error links to robot config');
assert.equal(await js(`return new URLSearchParams(location.search).get('robot')`),fixture.robotId);
assert.equal(await js(`return document.querySelector('input[aria-label="验证样本"]').value`),fixture.sources.joints.raw);
await textClick('返回原记录','a');await until(`return location.pathname==='/ingest/uploads/records'`,'return from config');
await textClick('重试处理');await until(`return document.body.innerText.includes('选择重试配置版本')`,'version chooser');
await click('input[aria-label="重试配置版本"]');await textClick('ROS MCAP · v2','.ant-select-item-option-content');
await shot('06-retry-version-diff');
await textClick('确认重试处理');
await until(`return !document.querySelector('.ant-modal-content')`,'retry accepted');
await until(`return document.body.innerText.includes('等待处理')`,'new generation pending');
const rootApi=`/projects/${fixture.projectId}/regions/${fixture.regionCode}/lerobot-imports/${fixture.sources.joints.raw}`;
const progress=await admin(rootApi+'/mcap-processing');assert.equal(progress.status,200,JSON.stringify(progress));
assert.equal(progress.data.history.length,2);assert.equal(progress.data.files[0].config_reference.version,2);assert.equal(progress.data.history[1].config_reference.version,1);
await shot('07-retry-preserves-history');result.checks.push('Selected v2 explicitly; new processing generation preserves v1 and its missing-image error');
await goto('/ingest/sources');
await until(`return document.body.innerText.includes('关节采集验收机器人')`,'onboarding list');
result.onboarding=await js('return document.body.innerText.slice(-7000)');await shot('08-onboarding-entry');
await writeFile(root+'/browser-stage2.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}catch(error){result.error=String(error);result.stack=error.stack;result.body=await js('return document.body.innerText.slice(0,13000)').catch(()=>null);await shot('browser-failure').catch(()=>{});await writeFile(root+'/browser-failure.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));process.exitCode=1;}
finally{if(sid)await js(`const s=JSON.parse(sessionStorage.getItem('hc-platform-current-session')||'null');if(s)return fetch('/api/v1/auth/session:logout',{method:'POST',headers:{Authorization:'Bearer '+s.sessionToken}})`).catch(()=>{});if(sid)await fetch('http://127.0.0.1:4446/session/'+sid,{method:'DELETE'});}
