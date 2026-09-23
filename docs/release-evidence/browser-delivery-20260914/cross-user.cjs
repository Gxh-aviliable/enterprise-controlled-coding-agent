const fs=require('fs');const assert=require('node:assert/strict');const crypto=require('crypto');
const owner=JSON.parse(fs.readFileSync(__dirname+'/fixture.json'));
const report=JSON.parse(fs.readFileSync(__dirname+'/e2e.json'));
const trace=report.cases.find(c=>c.name==='03-modify-approve-verify').trace_id;
async function api(path,token,method='GET',body){const r=await fetch('http://127.0.0.1:18000'+path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return {status:r.status,data:await r.json()};}
(async()=>{
 const id=crypto.randomBytes(6).toString('hex');const credentials={email:'goal-isolation-'+id+'@example.com',password:crypto.randomBytes(24).toString('base64url')};
 assert.equal((await api('/auth/register','', 'POST',{...credentials,username:'goal_isolation_'+id})).status,200);
 const login=await api('/auth/login','','POST',credentials);assert.equal(login.status,200);const token=login.data.access_token;
 const checks=[];
 for(const path of ['/tasks/'+trace,'/tasks/'+trace+'/trace','/tasks/'+trace+'/changes','/tasks/'+trace+'/events','/workspace/read?path=calculator.py']){
  const r=await api(path,token);assert.equal(r.status,404,path);checks.push({path,status:r.status});
 }
 const restore=await api('/tasks/'+trace+'/restore',token,'POST',{paths:['calculator.py'],expected_version:'0'.repeat(64)});assert.equal(restore.status,404);checks.push({operation:'cross-user restore',status:restore.status});
 for(const path of ['../user_'+owner.user_id+'/calculator.py','.agent/artifacts/guessed.json','.workspace-locks/evidence/guessed.json']){
  const r=await api('/workspace/read?path='+encodeURIComponent(path),token);console.log(JSON.stringify({path,status:r.status}));assert(r.status===400||r.status===403||r.status===404);checks.push({operation:'cross-user or operational path',path,status:r.status});
 }
 fs.writeFileSync(__dirname+'/cross-user.json',JSON.stringify({synthetic:true,passed:true,checks},null,2));console.log('Cross-user trace, events, changes, restore and operational paths denied');
})().catch(e=>{console.error(e.message);process.exitCode=1});
