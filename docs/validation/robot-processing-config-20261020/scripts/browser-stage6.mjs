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
async function clickElement(el){assert.ok(el,'element exists'); const id=el['element-6066-11e4-a52e-4f735466cecf'];for(let i=0;i<8;i++){try{await wd(`/session/${sid}/element/${id}/click`,{});return;}catch(e){if(i===7)throw e;await pause();}}}
async function click(css){await clickElement(await js('return document.querySelector(arguments[0])',[css]));}
async function textClick(label,selector='button'){const script=`return [...document.querySelectorAll(arguments[1])].find(e=>e.getBoundingClientRect().width>0 && [e.getAttribute('aria-label'),e.textContent].some(t=>t&&t.replace(/\\s/g,'')===arguments[0].replace(/\\s/g,'')))`;let el;for(let i=0;i<40;i++){el=await js(script,[label,selector]);if(el)break;await pause();}await clickElement(el);}
async function fill(css,value){const el=await js('return document.querySelector(arguments[0])',[css]);const id=el['element-6066-11e4-a52e-4f735466cecf'];await wd(`/session/${sid}/element/${id}/clear`,{});await wd(`/session/${sid}/element/${id}/value`,{text:value});}
async function shot(name){await pause();await pause();await pause();await js(`document.querySelectorAll('textarea[data-sensitive],textarea[aria-label="上传 Token"],textarea[aria-label="机器人平台凭据"]').forEach(e=>e.style.visibility='hidden')`);await writeFile(`${root}/${name}.png`,Buffer.from(await wd(`/session/${sid}/screenshot`),'base64'));}
async function admin(path,method='GET',body){return js(`const s=JSON.parse(sessionStorage.getItem('hc-platform-current-session'));return fetch('/api/v1'+arguments[0],{method:arguments[1],headers:{Authorization:'Bearer '+s.sessionToken,'X-Organization-Id':s.scope.organizationId,'X-Region-Code':s.scope.regionCode,'Content-Type':'application/json'},...(arguments[2]===null?{}:{body:JSON.stringify(arguments[2])})}).then(async r=>({status:r.status,data:r.status===204?null:await r.json()}))`,[path,method,body??null]);}
try {
const started=await wd('/session',{capabilities:{alwaysMatch:{browserName:'firefox',pageLoadStrategy:'eager','moz:firefoxOptions':{args:['-headless'],prefs:{'network.proxy.type':0}},timeouts:{pageLoad:30000,script:30000}}}});sid=started.sessionId;
await wd(`/session/${sid}/window/rect`,{width:1440,height:1120});
await goto('/');
const login=await js(`return fetch('/api/v1/auth/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'config-browser-admin',password:'ConfigTestOnly!2026'})}).then(async r=>({status:r.status,data:await r.json()}))`);
assert.equal(login.status,201,JSON.stringify(login));
await js(`sessionStorage.setItem('hc-platform-current-session',JSON.stringify({version:1,principal:{actorId:arguments[0].principal.principal_id,displayName:'Config test',roleIds:[]},sessionToken:arguments[0].access_token,scope:arguments[1]}))`,[login.data,{organizationId:fixture.organizationId,projectId:fixture.projectId,regionCode:fixture.regionCode}]);
await goto('/collection-tasks');await textClick('采集记录');await textClick('任务详情','[role="tab"]');
await until(`return [...document.querySelectorAll('label')].some(e=>e.innerText.includes('任务必须包含 RGB'))`,'task data requirement form');
const checkbox=`[...document.querySelectorAll('label')].find(e=>e.innerText.includes('任务必须包含 RGB')).querySelector('input')`;
assert.equal(await js('return '+checkbox+'.checked'),false);
await clickElement(await js('return '+checkbox));
await until(`return ${checkbox}.checked && !${checkbox}.disabled`,'visual requirement persisted');
const requirement=await admin('/projects/'+fixture.projectId+'/collection-tasks/'+fixture.taskId+'/data-requirements');
assert.equal(requirement.status,200);assert.equal(requirement.data.requires_rgb,true);
await shot('21-task-visual-requirement');
await goto('/collection-tasks');await textClick('采集记录');await textClick('任务详情','[role="tab"]');
await until(`return ${checkbox}?.checked`,'requirement retained after reload');
await clickElement(await js('return '+checkbox));
await until(`return !${checkbox}.checked && !${checkbox}.disabled`,'isolated requirement restored');
result.checks.push('Task visual requirement checkbox persisted through API and reload; restored isolated task afterward');
await writeFile(root+'/browser-stage6.json',JSON.stringify(result,null,2));console.log(JSON.stringify({checks:result.checks,error:result.error}));
}catch(error){result.error=String(error);result.stack=error.stack;result.body=await js('return document.body.innerText.slice(0,13000)').catch(()=>null);await shot('browser-failure').catch(()=>{});await writeFile(root+'/browser-failure.json',JSON.stringify(result,null,2));console.log(JSON.stringify({checks:result.checks,error:result.error}));process.exitCode=1;}
finally{if(sid)await js(`const s=JSON.parse(sessionStorage.getItem('hc-platform-current-session')||'null');if(s)return fetch('/api/v1/auth/session:logout',{method:'POST',headers:{Authorization:'Bearer '+s.sessionToken}})`).catch(()=>{});if(sid)await fetch('http://127.0.0.1:4446/session/'+sid,{method:'DELETE'});}
