import { computed, reactive, ref, watch } from 'vue'
import { experimentsApi, saveFile } from '../services/api.js'

const metadataColumns = new Set(['method', 'dataset', 'scenario', 'seed', 'run_id', 'status'])
const importStorageKey = 'research-library:last-experiment-import'
const draftStorageKey = 'research-library:saved-paper-drafts'

// 实验与写作页面共用同一份记录和分析范围，切换页面不重复读取或生成。
export function createExperimentWorkspace({ api = experimentsApi, storage = globalThis.localStorage, download = saveFile } = {}) {
  const planSetId = ref(''), uploadPlan = ref(null), file = ref(null), importIdInput = ref(''), imported = ref(null)
  const metrics = ref([]), dataset = ref(''), scenario = ref('')
  const statistics = ref([]), analysis = ref(null), draft = ref(null)
  const draftIdInput = ref('')
  const expansion = reactive({ section: 'introduction', instruction: '' })
  const expansionSuggestion = ref(null)
  const skillInstruction = ref(''), skillQuestion = ref('')
  const writing = reactive({ implementation_details: '', actual_experiment_setup: '' })
  const busy = ref(''), error = ref(''), message = ref('')
  const columns = computed(() => Object.keys(imported.value?.rows?.[0] || {}))
  const metricOptions = computed(() => columns.value.filter(name => !metadataColumns.has(name)))
  const datasets = computed(() => [...new Set((imported.value?.rows || []).map(row => row.dataset).filter(Boolean))])
  const scenarios = computed(() => [...new Set((imported.value?.rows || []).filter(row => row.dataset === dataset.value).map(row => row.scenario).filter(Boolean))])
  const previewRows = computed(() => (imported.value?.rows || []).slice(0, 20))

  function invalidateResults() { statistics.value = []; analysis.value = null; draft.value = null }
  watch([metrics, dataset, scenario], invalidateResults, { deep: true, flush: 'sync' })
  watch(writing, () => { draft.value = null }, { flush: 'sync' })
  // 建议只属于当前初稿和本次要求，切换后必须重新生成。
  watch([draft, () => expansion.section, () => expansion.instruction, skillInstruction], () => {
    expansionSuggestion.value = null; skillQuestion.value = ''
  }, { flush: 'sync' })
  async function run(action, work) {
    if (busy.value) return
    busy.value = action; error.value = ''; message.value = ''
    try { await work() } catch (cause) { error.value = cause.message }
    finally { busy.value = '' }
  }
  function positiveId(value, label) {
    const id = Number(value)
    if (!Number.isSafeInteger(id) || id < 1) throw new Error(`请输入有效的${label}编号。`)
    return id
  }
  function rememberedDrafts() {
    try { return JSON.parse(storage?.getItem(draftStorageKey) || '{}') } catch { return {} }
  }
  function rememberDraft(saved) {
    draftIdInput.value = String(saved.id)
    try { storage?.setItem(draftStorageKey, JSON.stringify({ ...rememberedDrafts(), [saved.import_id]: saved.id })) } catch {}
  }
  function applySavedDraft(saved) {
    const request = saved.writing_request
    metrics.value = [...request.metrics]; dataset.value = request.dataset; scenario.value = request.scenario
    Object.assign(writing, { implementation_details: request.implementation_details, actual_experiment_setup: request.actual_experiment_setup })
    // 参数恢复完成后再设置预览，避免范围变化的watch清空刚恢复的初稿。
    draft.value = saved
    rememberDraft(saved)
  }
  function changeDataset() {
    if (!scenarios.value.includes(scenario.value)) scenario.value = scenarios.value[0] || ''
  }
  function applyImport(record) {
    const changed = imported.value?.id !== record.id
    imported.value = record; importIdInput.value = String(record.id); planSetId.value = String(record.question_set_id)
    file.value = null; invalidateResults()
    metrics.value = metricOptions.value.filter(name => record.rows.some(row =>
      String(row[name] ?? '').trim() !== '' && Number.isFinite(Number(row[name]))))
    dataset.value = datasets.value[0] || ''; scenario.value = scenarios.value[0] || ''
    if (changed) { draftIdInput.value = ''; Object.assign(writing, { implementation_details: '', actual_experiment_setup: '' }) }
    try { storage?.setItem(importStorageKey, String(record.id)) } catch {}
  }
  async function loadPlan(id = planSetId.value) {
    await run('read-plan', async () => {
      uploadPlan.value = null
      const plan = await api.plan(positiveId(id, '问题集'))
      if (!plan) throw new Error('未找到研究方案，请先在“研究方案”页面生成并保存。')
      uploadPlan.value = plan; planSetId.value = String(plan.question_set_id); file.value = null
      message.value = `已载入问题集 ${plan.question_set_id} 的方案，可以上传实验结果。`
    })
  }
  async function loadImport(id = importIdInput.value) {
    await run('read-import', async () => {
      const record = await api.read(positiveId(id, '实验导入'))
      applyImport(record)
      if (uploadPlan.value?.question_set_id !== record.question_set_id) uploadPlan.value = null
      const draftId = rememberedDrafts()[record.id]
      if (draftId) {
        const saved = await api.readDraft(draftId)
        if (saved.import_id !== record.id) throw new Error('保存的初稿与当前实验记录不对应，请按初稿编号重新打开。')
        applySavedDraft(saved)
      }
      message.value = draft.value ? `已恢复实验记录 ${record.id} 和已保存初稿 ${draft.value.id}。` : `已载入实验导入记录 ${record.id}。`
    })
  }
  async function loadDraft(id = draftIdInput.value) {
    await run('read-draft', async () => {
      const saved = await api.readDraft(positiveId(id, '论文初稿'))
      const record = await api.read(saved.import_id)
      applyImport(record)
      if (uploadPlan.value?.question_set_id !== record.question_set_id) uploadPlan.value = null
      applySavedDraft(saved)
      message.value = `已打开保存的初稿 ${saved.id}，没有重新调用模型。`
    })
  }
  async function initialize() {
    let id
    try { id = storage?.getItem(importStorageKey) } catch {}
    if (id) await loadImport(id)
  }
  function chooseFile(event) {
    file.value = event.target.files?.[0] || null
    event.target.value = ''
  }
  async function upload() {
    await run('upload', async () => {
      if (!uploadPlan.value || !file.value) throw new Error('请先载入研究方案并选择CSV文件。')
      const record = await api.upload(uploadPlan.value.question_set_id, file.value)
      applyImport(record)
      message.value = `CSV已保存，实验导入编号为 ${record.id}。`
    })
  }
  function scopePayload() {
    if (!imported.value) throw new Error('请先上传或打开实验结果。')
    if (!dataset.value || !scenario.value || !metrics.value.length) throw new Error('请选择数据集、场景和至少一个指标。')
    return { metrics: [...metrics.value], dataset: dataset.value, scenario: scenario.value }
  }
  async function loadStatistics() {
    await run('statistics', async () => {
      const payload = scopePayload()
      const results = await Promise.all(payload.metrics.map(metric => api.statistics(imported.value.id, metric)))
      statistics.value = results.map(item => ({ ...item, groups: item.groups.filter(group =>
        group.dataset === payload.dataset && group.scenario === payload.scenario) }))
      message.value = '统计结果和图表已更新。'
    })
  }
  async function analyze() {
    await run('analysis', async () => {
      const result = await api.analyze(imported.value?.id, scopePayload())
      analysis.value = result; statistics.value = result.statistics
      message.value = '实验分析已生成。'
    })
  }
  async function exportReport() {
    await run('report-export', async () => {
      const blob = await api.exportReport(imported.value?.id, scopePayload())
      download(blob, `research-report-${imported.value.id}.zip`)
      message.value = '研究报告已开始下载。'
    })
  }
  function draftPayload() {
    return { ...scopePayload(), implementation_details: writing.implementation_details.trim(), actual_experiment_setup: writing.actual_experiment_setup.trim() }
  }
  async function generateDraft() {
    await run('draft', async () => {
      draft.value = await api.draft(imported.value?.id, draftPayload())
      rememberDraft(draft.value)
      message.value = `论文初稿 ${draft.value.id} 已生成并保存，可在下方预览。`
    })
  }
  async function exportDraft() {
    await run('draft-export', async () => {
      if (!draft.value?.id) throw new Error('请先生成或打开已保存的论文初稿。')
      const blob = await api.exportDraft(draft.value.id)
      download(blob, `paper-draft-${draft.value.id}.zip`)
      message.value = `初稿 ${draft.value.id} 已开始下载，没有重新调用模型。`
    })
  }
  async function expandDraft() {
    await run('draft-expand', async () => {
      if (!draft.value?.id) throw new Error('请先生成或打开已保存的论文初稿。')
      const instruction = expansion.instruction.trim()
      if (!instruction) throw new Error('请填写扩写要求。')
      expansionSuggestion.value = null
      expansionSuggestion.value = await api.expandDraft(draft.value.id, { section: expansion.section, instruction })
      message.value = '扩写建议已生成，原稿尚未修改。请核对后确认保存。'
    })
  }
  function discardExpansion() {
    expansionSuggestion.value = null
    message.value = '已放弃扩写建议，已保存的原稿保持不变。'
  }
  async function runDraftSkill() {
    await run('draft-skill', async () => {
      if (!draft.value?.id) throw new Error('请先生成或打开已保存的论文初稿。')
      const instruction = skillInstruction.value.trim()
      if (!instruction) throw new Error('请填写一句话扩写要求。')
      expansionSuggestion.value = null; skillQuestion.value = ''
      const result = await api.runDraftSkill(draft.value.id, instruction)
      if (result.status === 'needs_clarification') {
        skillQuestion.value = result.message
        return
      }
      expansion.section = result.suggestion.section
      expansion.instruction = result.instruction
      // 先同步识别出的参数，再设置建议，避免上面的watch清除新结果。
      expansionSuggestion.value = result.suggestion
      message.value = `${result.skill_name}：${result.message}`
    })
  }
  async function acceptExpansion() {
    await run('draft-save', async () => {
      const suggestion = expansionSuggestion.value
      if (!draft.value?.id || !suggestion) throw new Error('请先生成并核对扩写建议。')
      const content = { ...draft.value.content, [suggestion.section]: suggestion.paragraphs.map(paragraph => ({
        text: paragraph.text, source_numbers: [...paragraph.source_numbers],
      })) }
      const saved = await api.updateDraft(draft.value.id, content)
      draft.value = saved
      rememberDraft(saved)
      message.value = `初稿 ${saved.id} 的章节修改已保存，预览和下载均使用新正文。`
    })
  }
  return { planSetId, uploadPlan, file, importIdInput, imported, metrics, dataset, scenario, statistics, analysis, draft,
    writing, draftIdInput, expansion, expansionSuggestion, skillInstruction, skillQuestion, busy, error, message, columns, metricOptions, datasets, scenarios, previewRows,
    loadPlan, loadImport, loadDraft, initialize, chooseFile, upload, changeDataset, loadStatistics, analyze, exportReport, generateDraft, exportDraft,
    expandDraft, discardExpansion, acceptExpansion, runDraftSkill }
}
