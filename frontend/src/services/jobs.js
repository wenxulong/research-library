export const JOB_STORAGE_KEY = 'research-library.jobs.v1'
export const isActive = job => ['queued', 'running'].includes(job?.status)
export const jobKey = (kind, id) => `${kind}:${id}`
export function loadTracking(storage) {
  try { const value = JSON.parse(storage.getItem(JOB_STORAGE_KEY) || '[]'); return Array.isArray(value) ? value.filter(x => ['preparation', 'report'].includes(x.kind) && Number.isSafeInteger(x.jobId) && x.jobId > 0 && Number.isSafeInteger(x.ownerId) && x.ownerId > 0) : [] } catch { return [] }
}
export function saveTracking(storage, entries) {
  try { storage.setItem(JOB_STORAGE_KEY, JSON.stringify(entries.map(({ kind, jobId, ownerId }) => ({ kind, jobId, ownerId })))) } catch { /* 隐私模式/存储额度异常不阻止作业执行。 */ }
}
// 每个作业一个串行轮询；请求失败继续重试，关闭页面后清理计时器。
export function createWatcher({ fetchJob, update, error, delay = 2000, setTimer = setTimeout, clearTimer = clearTimeout }) {
  const entries = new Map(); let disposed = false
  function stop(kind, id) { const key = jobKey(kind, id); const entry = entries.get(key); if (entry?.timer) clearTimer(entry.timer); entries.delete(key) }
  async function start(record) {
    stop(record.kind, record.jobId)
    const key = jobKey(record.kind, record.jobId); const entry = {}; entries.set(key, entry)
    async function tick() {
      if (disposed || entries.get(key) !== entry) return
      let retry = true
      try {
        const job = await fetchJob(record.kind, record.jobId)
        if (disposed || entries.get(key) !== entry) return
        const owner = record.kind === 'preparation' ? job.paper_id : job.task_id
        if (owner !== record.ownerId) { error(record, '作业归属不匹配，请核对编号。'); retry = false }
        else { update(record, job); retry = isActive(job) }
      } catch (cause) {
        if (!disposed && entries.get(key) === entry) error(record, cause.message)
        if (cause.status === 404) retry = false
      }
      if (!disposed && entries.get(key) === entry) {
        if (retry) entry.timer = setTimer(tick, delay)
        else entries.delete(key)
      }
    }
    await tick()
  }
  return { start, stop, dispose() { disposed = true; for (const entry of entries.values()) if (entry.timer) clearTimer(entry.timer); entries.clear() } }
}
