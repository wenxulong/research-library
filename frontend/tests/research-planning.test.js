import test from 'node:test'
import assert from 'node:assert/strict'
import { researchPlanningApi } from '../src/services/api.js'
import { useResearchPlanning } from '../src/composables/useResearchPlanning.js'
import viteConfig from '../vite.config.js'

const record = () => ({ id: 7, task_id: 21, report_job_id: 17,
  candidates: [{ research_question: '问题一', source_numbers: [10] }, { research_question: '问题二', source_numbers: [13] }],
  selected_candidate_number: 1, selected_question: '问题一', sources: [] })
const savedPlan = () => ({ question_set_id: 7, research_question: '问题一', conditions: {
  equipment: 'Mac', data_resources: '已有数据', budget: '100元', time_budget: '四周', existing_implementation: '',
} })
function setup(overrides = {}, remembered = null) {
  const storage = { getItem: () => remembered, setItem: (_, value) => { remembered = value } }
  const api = { tasks: async () => [{ id: 21 }], reports: async () => [{ id: 17, status: 'succeeded' }],
    report: async () => ({ id: 17, status: 'succeeded', result: { passed: true } }),
    generate: async () => ({ ...record(), selected_candidate_number: null, selected_question: null }),
    read: async () => record(), plan: async () => savedPlan(),
    select: async (_, data) => ({ ...record(), selected_candidate_number: data.candidate_number, selected_question: data.research_question }),
    generatePlan: async (_, data) => ({ ...savedPlan(), conditions: data }), ...overrides }
  return { state: useResearchPlanning({ api, storage }), remembered: () => remembered }
}
test('production preview proxies saved research questions and plans to API', () => {
  const config = viteConfig({ mode: 'production' })
  assert.ok(config.preview.proxy['/research-question-sets'])
})

test('planning API uses correct read, selection and model routes', async t => {
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push([url, options.method, options.body && JSON.parse(options.body)])
    if (url.endsWith('/plan') && !options.method) return new Response('{"detail":"尚未生成"}', { status: 404 })
    return new Response('{}')
  })
  await researchPlanningApi.generate(17)
  await researchPlanningApi.read(7)
  await researchPlanningApi.select(7, { candidate_number: 2, research_question: '问题二' })
  assert.equal(await researchPlanningApi.plan(7), null)
  await researchPlanningApi.generatePlan(7, { equipment: 'Mac' })
  assert.deepEqual(calls, [
    ['/report-jobs/17/research-questions', 'POST', undefined], ['/research-question-sets/7', undefined, undefined],
    ['/research-question-sets/7/selection', 'PATCH', { candidate_number: 2, research_question: '问题二' }],
    ['/research-question-sets/7/plan', undefined, undefined], ['/research-question-sets/7/plan', 'POST', { equipment: 'Mac' }],
  ])
})
test('initialize restores last saved question and research conditions without generating', async () => {
  const { state } = setup({ generate: () => assert.fail('must not generate'), generatePlan: () => assert.fail('must not generate') }, '7')
  await state.initialize()
  assert.equal(state.questionSet.value.id, 7)
  assert.equal(state.taskId.value, 21)
  assert.equal(state.reportId.value, 17)
  assert.equal(state.conditions.equipment, 'Mac')
  assert.equal(state.selectionSaved.value, true)
})
test('new task clears previous question and plan, then loads its reports', async () => {
  const { state } = setup()
  await state.loadSet(7)
  state.taskId.value = 6
  await state.loadReports()
  assert.equal(state.questionSet.value, null)
  assert.equal(state.plan.value, null)
  assert.equal(state.reportId.value, '')
  assert.equal(state.reports.value[0].id, 17)
})
test('unreviewed report cannot trigger candidate model generation', async () => {
  const { state } = setup({ report: async () => ({ status: 'succeeded', result: { passed: false } }),
    generate: () => assert.fail('must not generate') })
  state.reportId.value = 17
  await state.generateCandidates()
  assert.match(state.error.value, /未通过审校/)
  assert.equal(state.busy.value, '')
})
test('generation remembers new set and waits for user selection', async () => {
  const { state, remembered } = setup()
  state.reportId.value = 17
  await state.generateCandidates()
  assert.equal(remembered(), '7')
  assert.equal(state.candidateNumber.value, null)
  assert.equal(state.selectionSaved.value, false)
  state.chooseCandidate(2)
  assert.equal(state.questionText.value, '问题二')
})
test('edited question must be saved and clears old plan only after successful save', async () => {
  const calls = []
  const { state } = setup({ select: async (id, data) => { calls.push([id, data]); return { ...record(), selected_candidate_number: data.candidate_number, selected_question: data.research_question } } })
  await state.loadSet(7)
  state.questionText.value = ' 修改后的问题 '
  assert.equal(state.selectionSaved.value, false)
  assert.ok(state.plan.value)
  await state.saveSelection()
  assert.deepEqual(calls, [[7, { candidate_number: 1, research_question: '修改后的问题' }]])
  assert.equal(state.plan.value, null)
  assert.equal(state.selectionSaved.value, true)
})
test('unchanged selection preserves saved plan', async () => {
  const { state } = setup()
  await state.loadSet(7)
  await state.saveSelection()
  assert.equal(state.plan.value.research_question, '问题一')
})
test('failed selection preserves existing saved record and permits retry', async () => {
  let fail = true
  const { state } = setup({ select: async () => { if (fail) throw new Error('保存失败'); return { ...record(), selected_question: '新问题' } } })
  await state.loadSet(7)
  state.questionText.value = '新问题'
  await state.saveSelection()
  assert.equal(state.questionSet.value.selected_question, '问题一')
  assert.ok(state.plan.value)
  assert.equal(state.selectionSaved.value, false)
  fail = false
  await state.saveSelection()
  assert.equal(state.selectionSaved.value, true)
  assert.equal(state.error.value, '')
})
test('plan generation submits trimmed actual conditions and does not save question again', async () => {
  const calls = []
  const { state } = setup({ generatePlan: async (id, data) => { calls.push([id, data]); return { ...savedPlan(), conditions: data } },
    select: () => assert.fail('must not resubmit selection') })
  await state.loadSet(7)
  state.conditions.equipment = ' Mac '
  await state.generatePlan()
  assert.equal(calls[0][0], 7)
  assert.equal(calls[0][1].equipment, 'Mac')
  assert.match(state.message.value, /已生成并保存/)
})
test('unsaved question or incomplete conditions do not call plan model', async () => {
  const { state } = setup({ generatePlan: () => assert.fail('must not generate') })
  await state.loadSet(7)
  state.questionText.value = '尚未保存'
  await state.generatePlan()
  assert.match(state.error.value, /先保存/)
  state.questionText.value = '问题一'; state.conditions.budget = ' '
  await state.generatePlan()
  assert.match(state.error.value, /填写设备/)
})
test('missing plan restores questions normally; nonexistent set displays server error', async () => {
  const { state } = setup({ plan: async () => null })
  await state.loadSet(7)
  assert.equal(state.error.value, '')
  assert.equal(state.plan.value, null)
  const missing = setup({ read: async () => { throw new Error('没有找到这组候选研究问题') } }).state
  await missing.loadSet(999)
  assert.match(missing.error.value, /没有找到/)
  assert.equal(missing.questionSet.value, null)
})
test('double submission while model runs creates only one set', async () => {
  let complete, count = 0
  const { state } = setup({ generate: () => { count++; return new Promise(resolve => { complete = resolve }) } })
  state.reportId.value = 17
  const pending = state.generateCandidates()
  await Promise.resolve()
  await state.generateCandidates()
  assert.equal(count, 1)
  complete(record())
  await pending
  assert.equal(state.busy.value, '')
})
