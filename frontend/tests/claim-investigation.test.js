import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ref } from 'vue'
import { createClaimInvestigation } from '../src/composables/useClaimInvestigation.js'
import { reportClaimsApi } from '../src/services/api.js'

const saved = (id=1) => ({ id, job_id: 19, claim_id: 'claim-a', result: { summary: '候选结果' } })
function setup(api) {
  const jobId=ref(19), claimId=ref('claim-a')
  return { ...createClaimInvestigation({jobId, claimId, api}), jobId, claimId }
}
test('trimmed submission returns independent candidate and blocks duplicate click', async()=>{
  let release, count=0
  const w=setup({investigate: (...args)=>{ count++; assert.deepEqual(args,[19,'claim-a','核对条件']); return new Promise(r=>release=r) }})
  w.instruction.value=' 核对条件 '
  const first=w.submit(); await w.submit()
  assert.equal(count,1); assert.equal(w.busy.value,true)
  release(saved()); await first
  assert.equal(w.selected.value.id,1); assert.equal(w.records.value.length,1)
  assert.equal(w.busy.value,false)
})
test('blank submission does not call model endpoint', async()=>{
  const w=setup({investigate:()=>{throw Error('must not call')}})
  w.instruction.value='  '; await w.submit(); assert.match(w.error.value,/请输入/)
})
test('adoption updates parent report and blocks duplicate click; dismiss never saves',async()=>{
 let release,count=0,adopted
 const jobId=ref(19),claimId=ref('claim-a')
 const w=createClaimInvestigation({jobId,claimId,onAdopted:r=>{adopted=r},api:{
  adopt:async id=>{count++;assert.equal(id,1);return new Promise(r=>release=r)}
 }})
 w.selected.value=saved()
 const pending=w.adopt();await w.adopt();assert.equal(count,1)
 release({job_id:19,checked_report:{passed:false}});await pending
 assert.equal(adopted.job_id,19);assert.ok(w.selected.value.adopted_at)
 assert.match(w.message.value,/已采用/)
 await w.adopt();assert.equal(count,1)
 w.dismiss();assert.equal(w.selected.value,null)
})
test('stale adoption error does not mark candidate adopted',async()=>{
 const w=setup({adopt:async()=>{throw Error('正式报告已变化')}})
 w.selected.value=saved();await w.adopt()
 assert.match(w.error.value,/已变化/);assert.equal(w.selected.value.adopted_at,undefined)
})
test('refresh recovery only reads saved history, then opens exact record', async()=>{
  const w=setup({history:async()=>[saved(2),saved(1)],read:async id=>saved(id)})
  await w.loadHistory(); assert.equal(w.selected.value.id,2)
  await w.openRecord(1); assert.equal(w.selected.value.id,1)
})
test('scope change discards late response and clears previous candidate', async()=>{
  let release
  const w=setup({investigate:()=>new Promise(r=>release=r)})
  w.instruction.value='查证'; const pending=w.submit()
  w.jobId.value=20; release(saved()); await pending
  assert.equal(w.selected.value,null); assert.deepEqual(w.records.value,[])
  assert.equal(w.instruction.value,''); assert.equal(w.busy.value,false)
})
test('errors are visible and record ownership is checked', async()=>{
  const w=setup({history:async()=>{throw Error('连接失败')},read:async()=>({...saved(),job_id:20})})
  await w.loadHistory(); assert.match(w.error.value,/连接失败/)
  await w.openRecord(1); assert.match(w.error.value,/不对应/); assert.equal(w.selected.value,null)
})
test('API wrappers use POST only for explicit investigation and GET for recovery', async()=>{
  const old=globalThis.fetch, calls=[]
  globalThis.fetch=async(url,options)=>{ calls.push({url,options}); return new Response('{}',{status:200}) }
  try {
    await reportClaimsApi.investigate(19,'claim-a','查证')
    await reportClaimsApi.history(19,'claim-a')
    await reportClaimsApi.read(2)
    assert.equal(calls[0].url,'/report-jobs/19/claims/claim-a/investigations')
    assert.equal(calls[0].options.method,'POST')
    assert.deepEqual(JSON.parse(calls[0].options.body),{instruction:'查证'})
    assert.match(calls[1].url,/investigations\?limit=10$/)
    assert.equal(calls[2].url,'/report-claim-investigations/2')
    assert.equal(calls[1].options.method,undefined)
  } finally { globalThis.fetch=old }
})
