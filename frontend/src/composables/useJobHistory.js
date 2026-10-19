import { ref } from 'vue'
import { papersApi, tasksApi } from '../services/api.js'
import { jobKey } from '../services/jobs.js'

// 历史来自服务器；浏览器跟踪列表仍由 useJobs 管理。
export function useJobHistory({ kind = 'report', resume, opened, api }) {
  // 两类作业的编号各自独立，查询与跟踪都必须带上类型。
  api ??= kind === 'preparation'
    ? { list: papersApi.list, history: papersApi.preparationHistory }
    : { list: tasksApi.list, history: tasksApi.reportHistory }
  const pageSize = 10
  const owners = ref([])
  const ownerId = ref('')
  const history = ref([])
  const loading = ref(false)
  const loaded = ref(false)
  const error = ref('')
  const page = ref(0)
  const hasMore = ref(false)
  const openingId = ref(null)
  let disposed = false

  async function loadOwners() {
    if (loading.value || openingId.value !== null || disposed) return
    loading.value = true
    error.value = ''
    try {
      const rows = await api.list()
      if (!disposed) owners.value = rows
    } catch (cause) {
      if (!disposed) error.value = cause.message
    } finally {
      if (!disposed) loading.value = false
    }
  }

  async function loadHistory(targetPage = 0) {
    if (loading.value || openingId.value !== null || disposed) return
    history.value = []
    loaded.value = false
    hasMore.value = false
    error.value = ''
    const id = Number(ownerId.value)
    if (!Number.isSafeInteger(id) || id <= 0) {
      page.value = 0
      return
    }
    loading.value = true
    // 多取一条来判断是否还有下一页，不需要额外统计全部记录。
    try {
      const rows = await api.history(id, pageSize + 1, targetPage * pageSize)
      if (disposed) return
      history.value = rows.slice(0, pageSize)
      hasMore.value = rows.length > pageSize
      page.value = targetPage
      loaded.value = true
    } catch (cause) {
      if (!disposed) error.value = cause.message
    } finally {
      if (!disposed) loading.value = false
    }
  }

  async function openJob(job) {
    if (loading.value || openingId.value !== null || disposed) return
    openingId.value = job.id
    error.value = ''
    try {
      // 获取完整作业，并复用现有轮询；历史摘要不包含结果正文。
      await resume(kind, job.id)
      if (!disposed) opened({ kind, key: jobKey(kind, job.id) })
    } catch (cause) {
      if (!disposed) error.value = cause.message
    } finally {
      if (!disposed) openingId.value = null
    }
  }

  function dispose() { disposed = true }
  return { owners, ownerId, history, loading, loaded, error, page, hasMore, openingId, loadOwners, loadHistory, openJob, dispose }
}
