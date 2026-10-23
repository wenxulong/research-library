import { computed, onMounted, onUnmounted, ref } from 'vue'
import { getJob } from '../services/api'
import { createWatcher, jobKey, loadTracking, saveTracking } from '../services/jobs'
export function useJobs(notify) {
  const storage = { getItem(key) { try { return localStorage.getItem(key) } catch { return null } }, setItem(key,value) { try { localStorage.setItem(key,value) } catch {} } }
  const entries = ref([])
  const watcher = createWatcher({ fetchJob: getJob, update(record, job) {
    const item = entries.value.find(x => x.key === jobKey(record.kind, record.jobId))
    if (item) { item.job = job; item.error = ''; if (job.status === 'failed') item.error = job.error_message || '作业失败，请检查服务后重新提交。' }
  }, error(record, message) { const item = entries.value.find(x => x.key === jobKey(record.kind, record.jobId)); if (item) item.error = message } })
  function track(kind, ownerId, job) {
    const key = jobKey(kind, job.id)
    const existing = entries.value.find(x => x.key === key)
    if (existing) { existing.job = job; existing.error = '' }
    else entries.value.unshift({ key, kind, ownerId, jobId: job.id, job, error: '' })
    saveTracking(storage, entries.value); watcher.start({ kind, ownerId, jobId: job.id })
  }
  async function resume(kind, id) {
    const job = await getJob(kind, id); const owner = kind === 'preparation' ? job.paper_id : job.task_id
    track(kind, owner, job); notify('已载入作业。')
  }
  function forget(item) { watcher.stop(item.kind, item.jobId); entries.value = entries.value.filter(x => x.key !== item.key); saveTracking(storage, entries.value) }
  function latest(kind, ownerId) { return entries.value.filter(x => x.kind === kind && x.ownerId === ownerId).sort((a,b) => b.jobId-a.jobId)[0] }
  onMounted(() => { for (const item of loadTracking(storage)) track(item.kind, item.ownerId, { id: item.jobId, status: 'loading', stage: '' }) })
  onUnmounted(() => watcher.dispose())
  return { entries, track, resume, forget, latest, activeCount: computed(() => entries.value.filter(x => ['queued','running'].includes(x.job?.status)).length) }
}
