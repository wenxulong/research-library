// 开发/预览使用Vite代理；构建挂到FastAPI时使用同源后端接口。
const base = import.meta.env?.DEV || import.meta.env?.MODE === 'preview' ? '/api' : ''
export class ApiError extends Error {
  constructor(message, status = 0, detail = null) {
    super(message); this.status = status; this.detail = detail
    this.jobId = detail?.job_id
  }
}
export function apiUrl(path) { return base + path }
export async function request(path, options = {}) {
  const { json, responseType = 'json', acceptedStatuses = [], signal: callerSignal, timeout = (options.method && options.method !== 'GET' ? 30000 : 15000), ...rest } = options
  const controller = new AbortController()
  const timer = timeout ? setTimeout(() => controller.abort(), timeout) : null
  const headers = new Headers(rest.headers)
  if (json !== undefined) headers.set('Content-Type', 'application/json')
  try {
    const response = await fetch(apiUrl(path), { ...rest, headers, body: json === undefined ? rest.body : JSON.stringify(json), signal: callerSignal ? AbortSignal.any([controller.signal, callerSignal]) : controller.signal })
    if (response.ok && responseType === 'blob') return await response.blob()
    const text = await response.text()
    let data = null
    try { data = text ? JSON.parse(text) : null } catch { if (response.ok) throw new ApiError('响应格式异常，请刷新核对结果。', response.status) }
    if (!response.ok && !acceptedStatuses.includes(response.status)) {
      const detail = data?.detail
      const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(x => x.msg).join('；') : detail?.message || `请求失败（${response.status}），请核对服务状态。`
      throw new ApiError(message, response.status, detail)
    }
    return data
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(rest.method && rest.method !== 'GET' ? '等待已结束，服务端操作可能仍在继续，提交结果未确认。请先刷新或查询状态，避免重复提交。' : '暂时无法读取数据，请检查连接并重试。')
  } finally { if (timer) clearTimeout(timer) }
}
export async function optional(path) { try { return await request(path) } catch (error) { if (error.status === 404) return null; throw error } }
export const papersApi = {
  list: () => request('/papers'),
  create: data => request('/papers', { method: 'POST', json: data }),
  update: (id, data) => request(`/papers/${id}`, { method: 'PUT', json: data }),
  remove: id => request(`/papers/${id}`, { method: 'DELETE' }),
  document: id => optional(`/papers/${id}/document`),
  upload: (id, file) => { const body = new FormData(); body.append('file', file); return request(`/papers/${id}/document`, { method: 'POST', body }) },
  removeDocument: id => request(`/papers/${id}/document`, { method: 'DELETE' }),
  prepare: id => request(`/papers/${id}/preparation-jobs`, { method: 'POST' }),
  preparationHistory: (id, limit = 20, offset = 0) => request(
    `/papers/${id}/preparation-jobs?${new URLSearchParams({ limit, offset })}`
  ),
  pages: id => request(`/papers/${id}/pages`),
  chunks: id => request(`/papers/${id}/chunks`),
}
// 模型操作允许较长等待，但仍有上限；停止等待不会取消服务端执行。
const modelOptions = signal => ({ timeout: 15 * 60 * 1000, signal })
export const tasksApi = {
  list: () => request('/research-tasks'),
  create: data => request('/research-tasks', { method: 'POST', json: data }),
  remove: id => request(`/research-tasks/${id}`, { method: 'DELETE' }),
  links: id => request(`/research-tasks/${id}/papers`),
  link: (id, paperId) => request(`/research-tasks/${id}/papers/${paperId}`, { method: 'POST', json: { note: '' } }),
  unlink: (id, paperId) => request(`/research-tasks/${id}/papers/${paperId}`, { method: 'DELETE' }),
  analysis: id => optional(`/research-tasks/${id}/analysis`),
  analyze: (id, signal) => request(`/research-tasks/${id}/analysis`, { method: 'POST', ...modelOptions(signal) }),
  review: id => optional(`/research-tasks/${id}/review`),
  runReview: (id, signal) => request(`/research-tasks/${id}/review`, { method: 'POST', ...modelOptions(signal) }),
  revise: (id, signal) => request(`/research-tasks/${id}/revisions/review`, { method: 'POST', ...modelOptions(signal) }),
  adopt: (id, base_analysis, revision, signal) => request(`/research-tasks/${id}/analysis/adopt-evidence-note`, { method: 'POST', json: { base_analysis, revision }, ...modelOptions(signal) }),
  versions: id => request(`/research-tasks/${id}/analysis/versions`),
  version: (id, versionId) => request(`/research-tasks/${id}/analysis/versions/${versionId}`),
  restore: (id, versionId, base_analysis) => request(`/research-tasks/${id}/analysis/versions/${versionId}/restore`, { method: 'POST', json: { base_analysis } }),
  answer: (id, question, top_k, signal) => request(`/research-tasks/${id}/answer`, { method: 'POST', json: { question, top_k }, ...modelOptions(signal) }),
  search: (id, q, top_k) => request(`/research-tasks/${id}/chunks/semantic-search?${new URLSearchParams({ q, top_k })}`),
  report: id => request(`/research-tasks/${id}/report-jobs`, { method: 'POST' }),
  // 历史查询只读取摘要；打开记录后再获取完整报告。
  reportHistory: (id, limit = 20, offset = 0) => request(
    `/research-tasks/${id}/report-jobs?${new URLSearchParams({ limit, offset })}`
  ),
}
export const getJob = (kind, id) => request(`/${kind === 'preparation' ? 'paper-preparation-jobs' : 'report-jobs'}/${id}`)
export const reportClaimsApi = {
  adopt: id => request(`/report-claim-investigations/${id}/adopt`, { method: 'POST' }),
  investigate: (jobId, claimId, instruction) => request(
    `/report-jobs/${jobId}/claims/${encodeURIComponent(claimId)}/investigations`,
    { method: 'POST', json: { instruction }, ...modelOptions() }
  ),
  history: (jobId, claimId) => request(
    `/report-jobs/${jobId}/claims/${encodeURIComponent(claimId)}/investigations?limit=10`
  ),
  read: id => request(`/report-claim-investigations/${id}`),
}
export const researchPlanningApi = {
  tasks: tasksApi.list,
  reports: id => tasksApi.reportHistory(id, 20, 0),
  report: id => getJob('report', id),
  generate: id => request(`/report-jobs/${id}/research-questions`, { method: 'POST', ...modelOptions() }),
  read: id => request(`/research-question-sets/${id}`),
  select: (id, data) => request(`/research-question-sets/${id}/selection`, { method: 'PATCH', json: data }),
  plan: id => optional(`/research-question-sets/${id}/plan`),
  generatePlan: (id, data) => request(`/research-question-sets/${id}/plan`, { method: 'POST', json: data, ...modelOptions() }),
}
export const experimentsApi = {
  plan: researchPlanningApi.plan,
  upload: (setId, file) => {
    const body = new FormData(); body.append('file', file)
    return request(`/research-question-sets/${setId}/experiment-imports`, { method: 'POST', body })
  },
  read: id => request(`/experiment-imports/${id}`),
  statistics: (id, metric) => request(`/experiment-imports/${id}/statistics?${new URLSearchParams({ metric })}`),
  chartUrl: (id, metric, dataset, scenario) => apiUrl(`/experiment-imports/${id}/chart?${new URLSearchParams({ metric, dataset, scenario })}`),
  analyze: (id, data) => request(`/experiment-imports/${id}/analysis`, { method: 'POST', json: data, ...modelOptions() }),
  exportReport: (id, data) => request(`/experiment-imports/${id}/research-report/export`, { method: 'POST', json: data, responseType: 'blob', ...modelOptions() }),
  draft: (id, data) => request(`/experiment-imports/${id}/paper-draft`, { method: 'POST', json: data, ...modelOptions() }),
  readDraft: id => request(`/paper-drafts/${id}`),
  expandDraft: (id, data) => request(`/paper-drafts/${id}/expand`, { method: 'POST', json: data, ...modelOptions() }),
  runDraftSkill: (id, instruction) => request(`/paper-drafts/${id}/skill`, { method: 'POST', json: { instruction }, ...modelOptions() }),
  updateDraft: (id, content) => request(`/paper-drafts/${id}`, { method: 'PATCH', json: content }),
  exportDraft: id => request(`/paper-drafts/${id}/export`, { responseType: 'blob', timeout: 60000 }),
}
export function saveFile(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a'); link.href = url; link.download = filename
  document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export async function downloadReport(id) {
  const response = await fetch(apiUrl(`/report-jobs/${id}/export`))
  if (!response.ok) { let data; try { data = await response.json() } catch {} throw new ApiError(typeof data?.detail === 'string' ? data.detail : '报告暂时无法下载。', response.status) }
  const blob = await response.blob(); const url = URL.createObjectURL(blob)
  const link = document.createElement('a'); link.href = url; link.download = `research-report-job-${id}.md`; document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
