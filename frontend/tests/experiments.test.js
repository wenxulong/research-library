import test from 'node:test'
import assert from 'node:assert/strict'
import { experimentsApi, ApiError } from '../src/services/api.js'
import { createExperimentWorkspace } from '../src/composables/useExperimentWorkspace.js'
import viteConfig from '../vite.config.js'

const conditions = { equipment: 'Mac', data_resources: '数据', budget: '100元', time_budget: '四周', existing_implementation: '' }
const plan = () => ({ question_set_id: 7, research_question: '测试问题', conditions, content: { result_columns: ['method', 'dataset', 'scenario', 'score'] } })
const record = () => ({ id: 9, question_set_id: 7, filename: 'results.csv', plan_snapshot: plan(), rows: [
  { method: 'baseline', dataset: 'demo', scenario: 'normal', seed: '1', run_id: '1', status: 'success', score: '.08', latency: '120', notes: '备注' },
  { method: 'ours', dataset: 'demo', scenario: 'shift', seed: '1', run_id: '2', status: 'success', score: '.04', latency: '160', notes: '备注' },
  { method: 'ours', dataset: 'other', scenario: 'separate', seed: '1', run_id: '3', status: 'success', score: '.09', latency: '180', notes: '备注' },
] })
const stats = metric => ({ import_id: 9, metric, status_available: true, groups: [
  { method: 'baseline', dataset: 'demo', scenario: 'normal', mean: .08 },
  { method: 'ours', dataset: 'demo', scenario: 'shift', mean: .04 },
] })
const savedDraft = () => ({ id: 42, import_id: 9, created_at: '2026-10-19T12:00:00', content: { title: '论文' },
  writing_request: { metrics: ['score'], dataset: 'demo', scenario: 'shift', implementation_details: '已保存方法', actual_experiment_setup: '已保存设置' } })
function setup(overrides = {}, remembered = null) {
  const downloads = []
  const values = new Map(remembered ? [['research-library:last-experiment-import', remembered]] : [])
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
  const api = { plan: async () => plan(), read: async () => record(), upload: async () => record(),
    statistics: async (_, metric) => stats(metric), analyze: async (_, data) => ({ statistics: data.metrics.map(stats), content: { summary: '分析' } }),
    exportReport: async () => new Blob(['zip']), draft: async () => savedDraft(), readDraft: async () => savedDraft(), exportDraft: async () => new Blob(['zip']), ...overrides }
  return { state: createExperimentWorkspace({ api, storage, download: (...args) => downloads.push(args) }), downloads,
    storage, remembered: () => storage.getItem('research-library:last-experiment-import') }
}

test('CSV upload sends multipart file without an invented Content-Type', async t => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/research-question-sets/7/experiment-imports')
    assert.equal(options.method, 'POST')
    assert.ok(options.body instanceof FormData)
    assert.equal(options.body.get('file').name, 'results.csv')
    assert.equal(options.headers.has('Content-Type'), false)
    return new Response('{"id":9}', { status: 201 })
  })
  assert.equal((await experimentsApi.upload(7, new File(['method,score\na,1'], 'results.csv'))).id, 9)
})
test('statistics and charts encode query values including Chinese and separators', async t => {
  t.mock.method(globalThis, 'fetch', async url => {
    assert.equal(url, '/experiment-imports/9/statistics?metric=score%26x')
    return new Response('{}')
  })
  await experimentsApi.statistics(9, 'score&x')
  const url = new URL(experimentsApi.chartUrl(9, 'score&x', '医疗 数据', 'normal/a'), 'http://localhost')
  assert.equal(url.searchParams.get('dataset'), '医疗 数据')
  assert.equal(url.searchParams.get('scenario'), 'normal/a')
  assert.equal(url.searchParams.get('metric'), 'score&x')
})
test('model routes use the exact analysis and writing payloads and binary exports', async t => {
  const scope = { metrics: ['score'], dataset: 'demo', scenario: 'normal' }
  const writing = { ...scope, implementation_details: '实际方法', actual_experiment_setup: '设置' }
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push([url, options.method, options.body ? JSON.parse(options.body) : undefined])
    return url.endsWith('/export') ? new Response(new Uint8Array([80, 75, 3, 4]), { headers: { 'Content-Type': 'application/zip' } }) : new Response('{}')
  })
  await experimentsApi.analyze(9, scope)
  assert.ok(await experimentsApi.exportReport(9, scope) instanceof Blob)
  await experimentsApi.draft(9, writing)
  assert.ok(await experimentsApi.exportDraft(42) instanceof Blob)
  assert.deepEqual(calls, [
    ['/experiment-imports/9/analysis', 'POST', scope], ['/experiment-imports/9/research-report/export', 'POST', scope],
    ['/experiment-imports/9/paper-draft', 'POST', writing], ['/paper-drafts/42/export', undefined, undefined],
  ])
})
test('failed binary download surfaces JSON error instead of downloading an error as ZIP', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('{"detail":"没有有效数值"}', { status: 409 }))
  await assert.rejects(experimentsApi.exportReport(9, {}), error => error instanceof ApiError && error.message === '没有有效数值')
})
test('production preview forwards experiment routes', () => {
  assert.ok(viteConfig({ mode: 'production' }).preview.proxy['/experiment-imports'])
  assert.ok(viteConfig({ mode: 'production' }).preview.proxy['/paper-drafts'])
})
test('restoring an import selects numerical metrics and keeps metadata out', async () => {
  const { state } = setup({}, '9')
  await state.initialize()
  assert.equal(state.imported.value.id, 9)
  assert.deepEqual(state.metrics.value, ['score', 'latency'])
  assert.equal(state.dataset.value, 'demo')
  assert.deepEqual(state.scenarios.value, ['normal', 'shift'])
  assert.equal(state.uploadPlan.value, null) // 旧快照不能冒充当前上传目标的最新方案。
})
test('upload attaches to loaded plan rather than an edited input id', async () => {
  const calls = []
  const { state, remembered } = setup({ upload: async (...args) => { calls.push(args); return record() } })
  await state.loadPlan(7)
  state.planSetId.value = '99'
  state.file.value = new File(['csv'], 'results.csv')
  await state.upload()
  assert.equal(calls[0][0], 7)
  assert.equal(state.imported.value.id, 9)
  assert.equal(state.file.value, null)
  assert.equal(remembered(), '9')
})
test('missing plan or file displays error and makes no upload', async () => {
  const { state } = setup({ plan: async () => null, upload: () => assert.fail('must not upload') })
  await state.loadPlan(7)
  assert.match(state.error.value, /未找到研究方案/)
  await state.upload()
  assert.match(state.error.value, /选择CSV/)
})
test('statistics filter to the same dataset and scenario as chart request', async () => {
  const { state } = setup()
  await state.loadImport(9)
  await state.loadStatistics()
  assert.equal(state.statistics.value.length, 2)
  assert.ok(state.statistics.value.every(item => item.groups.length === 1 && item.groups[0].scenario === 'normal'))
})
test('scope changes clear stale statistics, analysis and draft', async () => {
  const { state } = setup()
  await state.loadImport(9)
  state.statistics.value = [stats('score')]; state.analysis.value = {}; state.draft.value = {}
  state.dataset.value = 'other'
  state.changeDataset()
  assert.equal(state.scenario.value, 'separate')
  assert.deepEqual(state.statistics.value, [])
  assert.equal(state.analysis.value, null)
  assert.equal(state.draft.value, null)
})
test('editing actual research details invalidates draft but retains experimental analysis', async () => {
  const { state } = setup()
  await state.loadImport(9)
  state.analysis.value = { content: { summary: '分析' } }; state.draft.value = {}
  state.writing.implementation_details = '修改方法'
  assert.equal(state.draft.value, null)
  assert.ok(state.analysis.value)
})
test('opening another import clears old research details and generated results', async () => {
  const { state } = setup({ read: async id => ({ ...record(), id }) })
  await state.loadImport(9)
  state.writing.implementation_details = '旧方法'; state.draft.value = {}
  await state.loadImport(10)
  assert.equal(state.writing.implementation_details, '')
  assert.equal(state.draft.value, null)
})
test('analysis uses selected scope and exposes returned statistics', async () => {
  let payload
  const { state } = setup({ analyze: async (id, data) => { payload = [id, data]; return { statistics: [stats('score')], content: { summary: '分析' } } } })
  await state.loadImport(9)
  state.metrics.value = ['score']
  await state.analyze()
  assert.deepEqual(payload, [9, { metrics: ['score'], dataset: 'demo', scenario: 'normal' }])
  assert.equal(state.analysis.value.content.summary, '分析')
  assert.equal(state.statistics.value[0].metric, 'score')
})
test('draft requests use trimmed real details and do not save or resubmit research questions', async () => {
  let payload
  const { state } = setup({ draft: async (id, data) => { payload = [id, data]; return savedDraft() } })
  await state.loadImport(9)
  state.writing.implementation_details = ' 实际方法 '
  state.writing.actual_experiment_setup = ' 实际条件 '
  await state.generateDraft()
  assert.equal(payload[0], 9)
  assert.equal(payload[1].implementation_details, '实际方法')
  assert.equal(payload[1].actual_experiment_setup, '实际条件')
  assert.equal(state.draft.value.content.title, '论文')
})
test('ZIP exports call download only after success with correct filenames', async () => {
  const { state, downloads } = setup()
  await state.loadImport(9)
  await state.exportReport()
  await state.generateDraft()
  await state.exportDraft()
  assert.deepEqual(downloads.map(item => item[1]), ['research-report-9.zip', 'paper-draft-42.zip'])
  assert.ok(downloads.every(item => item[0] instanceof Blob))
})
test('failed export is retryable and never downloads an error response', async () => {
  let fail = true
  const { state, downloads } = setup({ exportDraft: async () => { if (fail) throw new Error('生成失败'); return new Blob(['zip']) } })
  await state.loadImport(9)
  await state.generateDraft()
  await state.exportDraft()
  assert.equal(state.error.value, '生成失败')
  assert.equal(state.busy.value, '')
  assert.equal(downloads.length, 0)
  fail = false
  await state.exportDraft()
  assert.equal(downloads.length, 1)
})
test('empty scope blocks model and download requests', async () => {
  const { state } = setup({ draft: () => assert.fail('must not generate'), exportReport: () => assert.fail('must not export') })
  await state.generateDraft()
  assert.match(state.error.value, /先上传或打开/)
  await state.loadImport(9)
  state.metrics.value = []
  await state.exportReport()
  assert.match(state.error.value, /至少一个指标/)
})
test('cross-page repeated actions cannot duplicate a pending model call', async () => {
  let complete, calls = 0
  const { state } = setup({ draft: () => { calls++; return new Promise(resolve => { complete = resolve }) },
    exportDraft: () => assert.fail('must not start parallel model operation') })
  await state.loadImport(9)
  const pending = state.generateDraft()
  await state.generateDraft()
  await state.exportDraft()
  assert.equal(calls, 1)
  complete(savedDraft())
  await pending
  assert.equal(state.busy.value, '')
})
test('generation remembers saved ID and refresh restores exact writing context using GET only', async () => {
  const first = setup()
  await first.state.loadImport(9)
  await first.state.generateDraft()
  assert.equal(first.state.draftIdInput.value, '42')
  const calls = []
  const restored = createExperimentWorkspace({ storage: first.storage, api: {
    read: async id => { calls.push(['import', id]); return record() },
    readDraft: async id => { calls.push(['draft', id]); return savedDraft() },
    draft: () => assert.fail('must not generate'),
  } })
  await restored.initialize()
  assert.deepEqual(calls, [['import', 9], ['draft', 42]])
  assert.equal(restored.draft.value.id, 42)
  assert.deepEqual(restored.metrics.value, ['score'])
  assert.equal(restored.scenario.value, 'shift')
  assert.equal(restored.writing.implementation_details, '已保存方法')
  assert.equal(restored.writing.actual_experiment_setup, '已保存设置')
})
test('opening a saved draft fetches its associated import and restores preview after form watches run', async () => {
  const { state } = setup()
  await state.loadDraft(42)
  assert.equal(state.imported.value.id, 9)
  assert.equal(state.draft.value.id, 42)
  assert.equal(state.draftIdInput.value, '42')
  assert.match(state.message.value, /没有重新调用模型/)
})
test('download uses saved draft ID only, with no generation and no dependence on current metrics', async () => {
  const calls = []
  const { state } = setup({ draft: () => assert.fail('must not generate'),
    exportDraft: async (...args) => { calls.push(args); return new Blob(['zip']) } })
  await state.loadDraft(42)
  await state.exportDraft()
  assert.deepEqual(calls, [[42]])
})
test('missing saved draft fails clearly instead of falling back to paid generation', async () => {
  const { state } = setup({ readDraft: async () => { throw new Error('没有找到这份论文初稿') }, draft: () => assert.fail('must not generate') })
  await state.loadDraft(99)
  assert.match(state.error.value, /没有找到/)
  assert.equal(state.draft.value, null)
  assert.equal(state.busy.value, '')
})
test('drafts are remembered per experiment and are not silently applied to another import', async () => {
  const { state } = setup({ read: async id => ({ ...record(), id }),
    readDraft: () => assert.fail('other import must not load draft 42') })
  await state.loadImport(9)
  await state.generateDraft()
  await state.loadImport(10)
  assert.equal(state.draft.value, null)
  assert.equal(state.draftIdInput.value, '')
})
test('download before generation or restore makes no API request', async () => {
  const { state } = setup({ exportDraft: () => assert.fail('must not download') })
  await state.loadImport(9)
  await state.exportDraft()
  assert.match(state.error.value, /先生成或打开已保存/)
})


test('chapter expansion and confirmation use POST suggestion then PATCH full content', async t => {
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push([url, options.method, JSON.parse(options.body)])
    return new Response('{}')
  })
  await experimentsApi.expandDraft(42, { section: 'methods', instruction: '展开实现步骤' })
  await experimentsApi.updateDraft(42, { title: '原标题', methods: [{ text: '新方法', source_numbers: [23] }] })
  assert.deepEqual(calls, [
    ['/paper-drafts/42/expand', 'POST', { section: 'methods', instruction: '展开实现步骤' }],
    ['/paper-drafts/42', 'PATCH', { title: '原标题', methods: [{ text: '新方法', source_numbers: [23] }] }],
  ])
})
const editableDraft = () => ({ ...savedDraft(), content: { title: '原标题', abstract: '原摘要',
  ...Object.fromEntries(['introduction', 'related_work', 'methods', 'experiments', 'discussion', 'conclusion'].map(field => [field, [{ text: field, source_numbers: [23] }]])) } })
const suggestion = () => ({ draft_id: 42, section: 'methods', paragraphs: [{ text: '扩写的方法', source_numbers: [41] }] })

test('suggestion does not save or mutate original and confirmation preserves other sections', async () => {
  const calls = []
  const original = editableDraft()
  const { state } = setup({ readDraft: async () => original,
    expandDraft: async (...args) => { calls.push(['expand', ...args]); return suggestion() },
    updateDraft: async (id, content) => { calls.push(['save', id, content]); return { ...original, content } },
  })
  await state.loadDraft(42)
  state.expansion.section = 'methods'; state.expansion.instruction = ' 展开步骤 '
  await state.expandDraft()
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0], ['expand', 42, { section: 'methods', instruction: '展开步骤' }])
  assert.equal(state.draft.value.content.methods[0].text, 'methods')
  state.expansionSuggestion.value.paragraphs[0].text = '人工核对后的方法'
  await state.acceptExpansion()
  assert.equal(calls.length, 2)
  assert.equal(calls[1][2].methods[0].text, '人工核对后的方法')
  for (const field of ['title', 'abstract', 'introduction', 'related_work', 'experiments', 'discussion', 'conclusion']) {
    assert.deepEqual(calls[1][2][field], original.content[field])
  }
  assert.equal(original.content.methods[0].text, 'methods')
  assert.equal(state.draft.value.content.methods[0].text, '人工核对后的方法')
  assert.equal(state.expansionSuggestion.value, null)
  assert.match(state.message.value, /修改已保存/)
})
test('discarding or changing chapter, instructions, scope or draft clears suggestions without saving', async () => {
  const { state } = setup({ readDraft: async () => editableDraft(),
    updateDraft: () => assert.fail('must not save without confirmation') })
  await state.loadDraft(42)
  state.expansionSuggestion.value = suggestion(); state.discardExpansion()
  assert.equal(state.expansionSuggestion.value, null)
  state.expansionSuggestion.value = suggestion(); state.expansion.section = 'methods'
  assert.equal(state.expansionSuggestion.value, null)
  state.expansionSuggestion.value = suggestion(); state.expansion.instruction = '修改要求'
  assert.equal(state.expansionSuggestion.value, null)
  state.expansionSuggestion.value = suggestion(); state.metrics.value = ['latency']
  assert.equal(state.expansionSuggestion.value, null)
  await state.loadDraft(42)
  state.expansionSuggestion.value = suggestion(); await state.loadDraft(42)
  assert.equal(state.expansionSuggestion.value, null)
})
test('failed save retains edited suggestion and original draft for retry', async () => {
  let fail = true
  const { state } = setup({ readDraft: async () => editableDraft(),
    updateDraft: async (_, content) => { if (fail) throw new Error('保存失败'); return { ...editableDraft(), content } } })
  await state.loadDraft(42)
  state.expansionSuggestion.value = suggestion()
  await state.acceptExpansion()
  assert.match(state.error.value, /保存失败/)
  assert.equal(state.expansionSuggestion.value.paragraphs[0].text, '扩写的方法')
  assert.equal(state.draft.value.content.methods[0].text, 'methods')
  fail = false
  await state.acceptExpansion()
  assert.equal(state.expansionSuggestion.value, null)
  assert.equal(state.draft.value.content.methods[0].text, '扩写的方法')
})
test('missing draft or blank instruction makes no model request', async () => {
  const { state } = setup({ expandDraft: () => assert.fail('must not call model') })
  await state.expandDraft()
  assert.match(state.error.value, /先生成或打开/)
  await state.loadDraft(42)
  state.expansion.instruction = '   '
  await state.expandDraft()
  assert.match(state.error.value, /填写扩写要求/)
})
test('failed generation leaves original unchanged and no stale suggestion', async () => {
  const { state } = setup({ readDraft: async () => editableDraft(), expandDraft: async () => { throw new Error('模型连接失败') } })
  await state.loadDraft(42)
  state.expansion.instruction = '展开步骤'
  state.expansionSuggestion.value = suggestion()
  await state.expandDraft()
  assert.match(state.error.value, /模型连接失败/)
  assert.equal(state.expansionSuggestion.value, null)
  assert.equal(state.draft.value.content.methods[0].text, 'methods')
})
test('pending expansion blocks duplicate generation and premature confirmation', async () => {
  let finish, calls = 0
  const { state } = setup({ expandDraft: () => { calls++; return new Promise(resolve => { finish = resolve }) }, updateDraft: () => assert.fail('must not save') })
  await state.loadDraft(42)
  state.expansion.instruction = '展开步骤'
  const pending = state.expandDraft()
  await state.expandDraft(); await state.acceptExpansion()
  assert.equal(calls, 1)
  finish(suggestion()); await pending
  assert.ok(state.expansionSuggestion.value)
})
