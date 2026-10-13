import test from 'node:test'
import assert from 'node:assert/strict'
import { request, ApiError, optional, papersApi, tasksApi, downloadReport } from '../src/services/api.js'
import { JOB_STORAGE_KEY, loadTracking, saveTracking, createWatcher } from '../src/services/jobs.js'
function mockFetch(t, handler) { t.mock.method(globalThis, 'fetch', handler) }
test('POST sends JSON to existing backend route', async t => {mockFetch(t,async(url,options)=>{assert.equal(url,'/papers');assert.equal(options.method,'POST');assert.equal(options.headers.get('Content-Type'),'application/json');assert.equal(JSON.parse(options.body).title,'论文');return new Response(JSON.stringify({id:9}),{status:201})});assert.equal((await papersApi.create({title:'论文'})).id,9)})
test('multipart upload preserves browser boundary', async t => {mockFetch(t,async(url,options)=>{assert.equal(url,'/papers/9/document');assert.equal(options.headers.has('Content-Type'),false);assert.ok(options.body instanceof FormData);assert.equal(options.body.get('file').name,'paper.pdf');return new Response('{}',{status:201})});await papersApi.upload(9,new File(['%PDF-'],'paper.pdf',{type:'application/pdf'}))})
test('503 dispatch failure retains queryable job id', async t=>{mockFetch(t,async()=>new Response(JSON.stringify({detail:{message:'投递失败',job_id:17}}),{status:503}));await assert.rejects(papersApi.prepare(9),error=>error instanceof ApiError&&error.jobId===17&&error.message==='投递失败')})
test('validation arrays become readable messages',async t=>{mockFetch(t,async()=>new Response(JSON.stringify({detail:[{msg:'年份无效'},{msg:'标题太长'}]}),{status:422}));await assert.rejects(request('/papers'),{message:'年份无效；标题太长'})})
test('health 503 data is displayed instead of discarded',async t=>{mockFetch(t,async()=>new Response(JSON.stringify({status:'degraded',checks:{worker:{status:'unknown'}}}),{status:503}));assert.equal((await request('/health/ready',{acceptedStatuses:[503]})).checks.worker.status,'unknown')})
test('only optional 404 becomes null',async t=>{mockFetch(t,async()=>new Response('{}',{status:404}));assert.equal(await optional('/x'),null);t.mock.restoreAll();mockFetch(t,async()=>new Response('{}',{status:409}));await assert.rejects(optional('/x'),error=>error.status===409)})
test('unknown mutation result warns against repeating blindly',async t=>{mockFetch(t,async()=>{throw Error('network')});await assert.rejects(papersApi.create({}),error=>error.message.includes('提交结果未确认'))})
test('successful HTML response cannot be mistaken for JSON',async t=>{mockFetch(t,async()=>new Response('<html>wrong route</html>'));await assert.rejects(request('/papers'),error=>error instanceof ApiError&&error.message.includes('响应格式异常'))})
test('task linking sends expected note payload',async t=>{mockFetch(t,async(url,options)=>{assert.equal(url,'/research-tasks/10/papers/9');assert.deepEqual(JSON.parse(options.body),{note:''});return new Response('{}',{status:201})});await tasksApi.link(10,9)})
test('history restore sends base snapshot for concurrency check',async t=>{const analysis={task_id:10,summary:'原分析'};mockFetch(t,async(url,options)=>{assert.equal(url,'/research-tasks/10/analysis/versions/3/restore');assert.deepEqual(JSON.parse(options.body),{base_analysis:analysis});return new Response('{}')});await tasksApi.restore(10,3,analysis)})
test('stored tracking filters corrupt owner ids and types',()=>{const storage={getItem:()=>JSON.stringify([{kind:'report',jobId:16,ownerId:10},{kind:'report',jobId:-1,ownerId:10},{kind:'unknown',jobId:3,ownerId:2},{kind:'preparation',jobId:2,ownerId:'9'}])};assert.deepEqual(loadTracking(storage),[{kind:'report',jobId:16,ownerId:10}])})
test('broken storage does not prevent submitting or reading',()=>{assert.deepEqual(loadTracking({getItem(){throw Error('blocked')}}),[]);assert.doesNotThrow(()=>saveTracking({setItem(){throw Error('quota')}},[]))})
test('tracking saves ids only, never report text',()=>{let saved;saveTracking({setItem(key,value){assert.equal(key,JOB_STORAGE_KEY);saved=JSON.parse(value)}},[{kind:'report',jobId:16,ownerId:10,job:{result:'private text'}}]);assert.deepEqual(saved,[{kind:'report',jobId:16,ownerId:10}])})
test('polls are serial and stop at terminal state',async()=>{const callbacks=[];let calls=0;const updates=[];const watcher=createWatcher({fetchJob:async()=>({paper_id:9,status:++calls===1?'running':'succeeded'}),update:(r,j)=>updates.push(j.status),error:()=>assert.fail(),setTimer:fn=>{callbacks.push(fn);return callbacks.length},clearTimer:()=>{}});await watcher.start({kind:'preparation',ownerId:9,jobId:3});assert.deepEqual(updates,['running']);await callbacks.shift()();assert.deepEqual(updates,['running','succeeded']);assert.equal(callbacks.length,0);watcher.dispose()})
test('temporary poll error retries instead of declaring failure',async()=>{const timers=[];let calls=0;const errors=[];const updates=[];const watcher=createWatcher({fetchJob:async()=>{if(++calls===1)throw new ApiError('暂时不可用');return {task_id:10,status:'succeeded'}},update:(r,j)=>updates.push(j.status),error:(r,e)=>errors.push(e),setTimer:fn=>{timers.push(fn);return 1},clearTimer:()=>{}});await watcher.start({kind:'report',ownerId:10,jobId:16});assert.equal(errors.length,1);await timers.shift()();assert.deepEqual(updates,['succeeded']);watcher.dispose()})
test('owner mismatch is never shown under another paper',async()=>{let updated=false,timed=false;const errors=[];const watcher=createWatcher({fetchJob:async()=>({paper_id:3,status:'running'}),update:()=>{updated=true},error:(r,e)=>errors.push(e),setTimer:()=>{timed=true},clearTimer:()=>{}});await watcher.start({kind:'preparation',ownerId:9,jobId:3});assert.equal(updated,false);assert.equal(timed,false);assert.match(errors[0],/归属不匹配/);watcher.dispose()})
test('unmount discards in-flight response and schedules no timer',async()=>{let resolve;let updates=0;const watcher=createWatcher({fetchJob:()=>new Promise(r=>{resolve=r}),update:()=>updates++,error:()=>{},setTimer:()=>assert.fail(),clearTimer:()=>{}});const promise=watcher.start({kind:'report',ownerId:10,jobId:16});watcher.dispose();resolve({task_id:10,status:'running'});await promise;assert.equal(updates,0)})
test('404 stops polling instead of an endless retry',async()=>{let message;const watcher=createWatcher({fetchJob:async()=>{throw new ApiError('不存在',404)},update:()=>assert.fail(),error:(r,e)=>{message=e},setTimer:()=>assert.fail(),clearTimer:()=>{}});await watcher.start({kind:'report',ownerId:10,jobId:16});assert.equal(message,'不存在');watcher.dispose()})

test('report download uses checked export and starts local file save',async t=>{let clicked=false;const anchor={click(){clicked=true},remove(){}};Object.defineProperty(globalThis,'document',{configurable:true,value:{createElement:()=>anchor,body:{appendChild(){}}}});t.after(()=>delete globalThis.document);mockFetch(t,async url=>{assert.equal(url,'/report-jobs/16/export');return new Response('中文报告',{headers:{'content-type':'text/markdown; charset=utf-8'}})});await downloadReport(16);assert.equal(clicked,true);assert.equal(anchor.download,'research-report-job-16.md');assert.match(anchor.href,/^blob:/)})
test('failed report export does not attempt to save a file',async t=>{mockFetch(t,async()=>new Response(JSON.stringify({detail:'报告尚未完成'}),{status:409}));await assert.rejects(downloadReport(16),error=>error.status===409&&error.message==='报告尚未完成')})

test('ordinary mutation has a finite default timeout', async t=>{
 let duration;const original=setTimeout
 t.mock.method(globalThis,'setTimeout',(fn,ms)=>{duration=ms;return original(fn,ms)})
 mockFetch(t,async()=>new Response('{}'))
 await papersApi.create({});assert.equal(duration,30000)
})
test('hanging mutation stops waiting and never reports server cancellation',async t=>{
 mockFetch(t,async(url,options)=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true})))
 await assert.rejects(request('/papers',{method:'POST',timeout:5}),error=>error.message.includes('服务端操作可能仍在继续')&&error.message.includes('避免重复提交'))
})
test('model request accepts caller cancellation and retains result uncertainty',async t=>{
 const controller=new AbortController();let duration
 const original=setTimeout;t.mock.method(globalThis,'setTimeout',(fn,ms)=>{duration=ms;return original(fn,ms)})
 mockFetch(t,async(url,options)=>new Promise((resolve,reject)=>{assert.equal(url,'/research-tasks/10/analysis');options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true})}))
 const pending=tasksApi.analyze(10,controller.signal);controller.abort()
 await assert.rejects(pending,error=>error.message.includes('提交结果未确认'))
 assert.equal(duration,900000)
})
