import test from 'node:test'
import assert from 'node:assert/strict'
import { tasksApi } from '../src/services/api.js'
import { useJobHistory } from '../src/composables/useJobHistory.js'

function setup(overrides = {}) {
  return useJobHistory({ resume: async () => {}, opened: () => {}, api: { list: async () => [], history: async () => [], ...overrides.api }, ...overrides })
}
test('report history GET sends task and pagination without creating a job', async t => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/research-tasks/6/report-jobs?limit=11&offset=10')
    assert.equal(options.method, undefined)
    assert.equal(options.body, undefined)
    return new Response('[{"id":17}]')
  })
  assert.deepEqual(await tasksApi.reportHistory(6, 11, 10), [{ id: 17 }])
})
test('one extra row controls pagination without hiding a row on the next page', async () => {
  const calls = []
  const state = setup({ api: { history: async (...args) => {
    calls.push(args)
    return Array.from({ length: args[2] === 0 ? 11 : 1 }, (_, i) => ({ id: 20 - args[2] - i }))
  } } })
  state.ownerId.value = 6
  await state.loadHistory()
  assert.equal(state.history.value.length, 10)
  assert.equal(state.hasMore.value, true)
  await state.loadHistory(1)
  assert.deepEqual(calls, [[6, 11, 0], [6, 11, 10]])
  assert.equal(state.history.value[0].id, 10)
  assert.equal(state.hasMore.value, false)
  assert.equal(state.page.value, 1)
})
test('exactly ten rows is the final page', async () => {
  const state = setup({ api: { history: async () => Array.from({ length: 10 }, (_, id) => ({ id })) } })
  state.ownerId.value = 6
  await state.loadHistory()
  assert.equal(state.hasMore.value, false)
})
test('switching task discards old rows even when new query fails, then allows retry', async () => {
  let fail = false
  const state = setup({ api: { history: async () => { if (fail) throw new Error('任务已删除'); return [{ id: 4 }] } } })
  state.ownerId.value = 6
  await state.loadHistory()
  fail = true
  state.ownerId.value = 9
  await state.loadHistory()
  assert.deepEqual(state.history.value, [])
  assert.equal(state.loaded.value, false)
  assert.equal(state.loading.value, false)
  assert.equal(state.error.value, '任务已删除')
  fail = false
  await state.loadHistory()
  assert.equal(state.error.value, '')
  assert.equal(state.loaded.value, true)
})
test('clearing task makes no request and resets pagination', async () => {
  const state = setup({ api: { history: async () => assert.fail('must not query') } })
  state.page.value = 2
  await state.loadHistory()
  assert.equal(state.page.value, 0)
  assert.equal(state.loaded.value, false)
})
test('opening history uses the full-job tracker and only expands after successful lookup', async () => {
  const calls = []
  const state = setup({ resume: async (...args) => calls.push(args), opened: key => calls.push(key) })
  await state.openJob({ id: 17 })
  assert.deepEqual(calls, [['report', 17], { kind: 'report', key: 'report:17' }])
  assert.equal(state.openingId.value, null)
})
test('failed detail lookup does not open a report and can be retried', async () => {
  let fail = true
  let opened = 0
  const state = setup({ resume: async () => { if (fail) throw new Error('作业不存在') }, opened: () => opened++ })
  await state.openJob({ id: 17 })
  assert.equal(opened, 0)
  assert.equal(state.error.value, '作业不存在')
  assert.equal(state.openingId.value, null)
  fail = false
  await state.openJob({ id: 17 })
  assert.equal(opened, 1)
})
test('unmounted history ignores a late response', async () => {
  let resolve
  const state = setup({ api: { history: () => new Promise(r => { resolve = r }) } })
  state.ownerId.value = 6
  const pending = state.loadHistory()
  state.dispose()
  resolve([{ id: 17 }])
  await pending
  assert.deepEqual(state.history.value, [])
  assert.equal(state.loaded.value, false)
})
test('preparation history GET uses paper route and pagination', async t => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/papers/3/preparation-jobs?limit=11&offset=10')
    assert.equal(options.method, undefined)
    assert.equal(options.body, undefined)
    return new Response('[{"id":10,"document_id":2}]')
  })
  const { papersApi } = await import('../src/services/api.js')
  assert.equal((await papersApi.preparationHistory(3, 11, 10))[0].document_id, 2)
})
test('preparation default adapter loads papers and their history', async t => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async url => {
    requests.push(url)
    return new Response(JSON.stringify(url === '/papers' ? [{ id: 3, title: '论文' }] : [{ id: 10 }]))
  })
  const state = useJobHistory({ kind: 'preparation', resume: async () => {}, opened: () => {} })
  await state.loadOwners()
  state.ownerId.value = 3
  await state.loadHistory()
  assert.deepEqual(requests, ['/papers', '/papers/3/preparation-jobs?limit=11&offset=0'])
  assert.equal(state.owners.value[0].id, 3)
  assert.equal(state.history.value[0].id, 10)
})
test('same id in both job types is tracked under different keys', async () => {
  const calls = []
  for (const kind of ['report', 'preparation']) {
    const state = setup({ kind, resume: async (...args) => calls.push(args), opened: value => calls.push(value) })
    await state.openJob({ id: 4 })
  }
  assert.deepEqual(calls, [
    ['report', 4], { kind: 'report', key: 'report:4' },
    ['preparation', 4], { kind: 'preparation', key: 'preparation:4' },
  ])
})
test('switching history type disposes old requests without contaminating new rows', async () => {
  let resolve
  const old = setup({ api: { history: () => new Promise(r => { resolve = r }) } })
  old.ownerId.value = 6
  const pending = old.loadHistory()
  old.dispose()
  const current = setup({ kind: 'preparation', api: { history: async () => [{ id: 10, paper_id: 3 }] } })
  current.ownerId.value = 3
  await current.loadHistory()
  resolve([{ id: 4, task_id: 6 }])
  await pending
  assert.deepEqual(old.history.value, [])
  assert.deepEqual(current.history.value, [{ id: 10, paper_id: 3 }])
})
test('repeated open while detail is loading does not duplicate lookup', async () => {
  let resolve
  let calls = 0
  const state = setup({ resume: () => { calls++; return new Promise(r => { resolve = r }) } })
  const pending = state.openJob({ id: 4 })
  await state.openJob({ id: 4 })
  assert.equal(calls, 1)
  resolve()
  await pending
})
