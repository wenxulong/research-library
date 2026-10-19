import { computed, reactive, ref } from 'vue'
import { researchPlanningApi } from '../services/api.js'

const storageKey = 'research-library:last-question-set'
const emptyConditions = () => ({ equipment: '', data_resources: '', budget: '', time_budget: '', existing_implementation: '' })

// 只记住最近打开的编号；问题和方案始终从服务器读取。
export function useResearchPlanning({ api = researchPlanningApi, storage = globalThis.localStorage } = {}) {
  const tasks = ref([]), taskId = ref(''), reports = ref([]), reportId = ref('')
  const questionSet = ref(null), plan = ref(null), setIdInput = ref('')
  const candidateNumber = ref(null), questionText = ref('')
  const conditions = reactive(emptyConditions())
  const busy = ref(''), error = ref(''), message = ref('')
  const selectionDirty = computed(() => !!questionSet.value && (
    candidateNumber.value !== questionSet.value.selected_candidate_number ||
    questionText.value.trim() !== (questionSet.value.selected_question || '')
  ))
  const selectionSaved = computed(() => !!questionSet.value?.selected_question && !selectionDirty.value)

  async function run(action, work) {
    if (busy.value) return
    busy.value = action; error.value = ''; message.value = ''
    try { await work() } catch (cause) { error.value = cause.message }
    finally { busy.value = '' }
  }
  function remember(id) { try { storage?.setItem(storageKey, String(id)) } catch {} }
  function clearWorkspace() {
    questionSet.value = null; plan.value = null; candidateNumber.value = null; questionText.value = ''
    Object.assign(conditions, emptyConditions())
  }
  function applyRecord(record) {
    questionSet.value = record
    candidateNumber.value = record.selected_candidate_number
    questionText.value = record.selected_question || ''
    setIdInput.value = String(record.id)
    remember(record.id)
  }
  async function loadTasks() {
    await run('tasks', async () => { tasks.value = await api.tasks() })
  }
  async function loadReports() {
    await run('reports', async () => {
      reports.value = []; reportId.value = ''; clearWorkspace()
      if (taskId.value) reports.value = await api.reports(Number(taskId.value))
    })
  }
  async function loadSet(id = setIdInput.value) {
    await run('read', async () => {
      const number = Number(id)
      if (!Number.isSafeInteger(number) || number < 1) throw new Error('请输入有效的问题集编号。')
      clearWorkspace()
      const record = await api.read(number)
      applyRecord(record)
      taskId.value = record.task_id; reportId.value = record.report_job_id
      // 已保存的方案若因修改问题被清除，接口返回404，页面正常显示待生成。
      plan.value = await api.plan(record.id)
      if (plan.value) Object.assign(conditions, plan.value.conditions)
      reports.value = []
      message.value = `已载入问题集 ${record.id}。`
    })
  }
  async function initialize() {
    await loadTasks()
    let id
    try { id = storage?.getItem(storageKey) } catch {}
    if (id) await loadSet(id)
  }
  async function generateCandidates() {
    await run('questions', async () => {
      if (!reportId.value) throw new Error('请先选择一份对比报告。')
      const report = await api.report(Number(reportId.value))
      if (report.status !== 'succeeded' || !report.result?.passed) {
        throw new Error('这份报告尚未完成或未通过审校，请先在“作业与报告”中处理。')
      }
      const record = await api.generate(report.id)
      clearWorkspace(); applyRecord(record)
      message.value = `候选问题已保存为问题集 ${record.id}。`
    })
  }
  function chooseCandidate(number) {
    candidateNumber.value = number
    questionText.value = questionSet.value.candidates[number - 1].research_question
  }
  async function saveSelection() {
    await run('selection', async () => {
      if (!candidateNumber.value || !questionText.value.trim()) throw new Error('请选择候选问题，并填写最终问题表述。')
      if (questionText.value.trim().length > 2000) throw new Error('问题表述不能超过2000字。')
      const changed = selectionDirty.value
      const record = await api.select(questionSet.value.id, {
        candidate_number: candidateNumber.value, research_question: questionText.value.trim(),
      })
      applyRecord(record)
      if (changed) plan.value = null
      message.value = changed ? '研究问题已保存，可以生成方案。原有方案需重新生成。' : '已确认当前研究问题。'
    })
  }
  async function generatePlan() {
    await run('plan', async () => {
      if (!selectionSaved.value) throw new Error('请先保存最终研究问题。')
      const data = Object.fromEntries(Object.entries(conditions).map(([key, value]) => [key, value.trim()]))
      if (['equipment', 'data_resources', 'budget', 'time_budget'].some(key => !data[key])) {
        throw new Error('请填写设备、数据资源、预算和时间安排。')
      }
      plan.value = await api.generatePlan(questionSet.value.id, data)
      message.value = '研究方案已生成并保存。'
    })
  }
  return { tasks, taskId, reports, reportId, questionSet, plan, setIdInput, candidateNumber, questionText,
    conditions, busy, error, message, selectionDirty, selectionSaved,
    loadTasks, loadReports, loadSet, initialize, generateCandidates, chooseCandidate, saveSelection, generatePlan }
}
