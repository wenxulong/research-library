import { ref, watch } from 'vue'
import { reportClaimsApi } from '../services/api.js'

// 只保存候选；每次独立请求均以当前正式陈述为上下文，不暗示已有多轮聊天记忆。
export function createClaimInvestigation({ jobId, claimId, api = reportClaimsApi, onAdopted = () => {} }) {
  const instruction = ref(''), records = ref([]), selected = ref(null)
  const busy = ref(false), error = ref(''), message = ref('')
  let version = 0
  watch([jobId, claimId], () => {
    version++; instruction.value = ''; records.value = []; selected.value = null
    error.value = ''; message.value = ''; busy.value = false
  }, { flush: 'sync' })

  async function run(work) {
    if (busy.value) return
    const ticket = version
    busy.value = true; error.value = ''; message.value = ''
    try { await work(() => ticket === version) }
    catch (cause) { if (ticket === version) error.value = cause.message }
    finally { if (ticket === version) busy.value = false }
  }
  async function loadHistory() {
    return run(async current => {
      const data = await api.history(jobId.value, claimId.value)
      if (!current()) return
      records.value = data
      selected.value = data[0] || null
    })
  }
  async function submit() {
    return run(async current => {
      const text = instruction.value.trim()
      if (!text) throw new Error('请输入查证要求。')
      selected.value = null
      const saved = await api.investigate(jobId.value, claimId.value, text)
      if (!current()) return
      selected.value = saved
      records.value = [saved, ...records.value.filter(x => x.id !== saved.id)].slice(0, 10)
    })
  }
  async function openRecord(id) {
    return run(async current => {
      const record = await api.read(id)
      if (!current()) return
      if (record.job_id !== jobId.value || record.claim_id !== claimId.value) {
        throw new Error('查证记录与当前陈述不对应。')
      }
      selected.value = record
    })
  }
  async function adopt() {
    return run(async current => {
      const record = selected.value
      if (!record || record.adopted_at) throw new Error('请先选择尚未采用的查证记录。')
      const result = await api.adopt(record.id)
      if (!current()) return
      selected.value = { ...record, adopted_at: new Date().toISOString() }
      records.value = records.value.map(x => x.id === record.id ? selected.value : x)
      onAdopted(result)
      message.value = '证据与复审结果已采用，正式报告和下载已更新。'
    })
  }
  function dismiss() { if (!busy.value) selected.value = null }
  return { instruction, records, selected, busy, error, message, loadHistory, submit, openRecord, adopt, dismiss }
}
