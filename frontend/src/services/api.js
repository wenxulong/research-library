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
  const { json, acceptedStatuses = [], signal: callerSignal, timeout = (options.method && options.method !== 'GET' ? 30000 : 15000), ...rest } = options
  const controller = new AbortController()
  const timer = timeout ? setTimeout(() => controller.abort(), timeout) : null
  const headers = new Headers(rest.headers)
  if (json !== undefined) headers.set('Content-Type', 'application/json')
  try {
    const response = await fetch(apiUrl(path), { ...rest, headers, body: json === undefined ? rest.body : JSON.stringify(json), signal: callerSignal ? AbortSignal.any([controller.signal, callerSignal]) : controller.signal })
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
}
export const getJob = (kind, id) => request(`/${kind === 'preparation' ? 'paper-preparation-jobs' : 'report-jobs'}/${id}`)
export async function downloadReport(id) {
  const response = await fetch(apiUrl(`/report-jobs/${id}/export`))
  if (!response.ok) { let data; try { data = await response.json() } catch {} throw new ApiError(typeof data?.detail === 'string' ? data.detail : '报告暂时无法下载。', response.status) }
  const blob = await response.blob(); const url = URL.createObjectURL(blob)
  const link = document.createElement('a'); link.href = url; link.download = `research-report-job-${id}.md`; document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
