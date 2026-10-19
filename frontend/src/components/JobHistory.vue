<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useJobHistory } from '../composables/useJobHistory'
import { dateText } from '../services/labels'
import JobSummary from './JobSummary.vue'

const props = defineProps({ jobs: Object, kind: { type: String, default: 'report' } })
const preparation = computed(() => props.kind === 'preparation')
const emit = defineEmits(['opened'])
const {
  owners, ownerId, history, loading, loaded, error, page, hasMore, openingId,
  loadOwners, loadHistory, openJob, dispose,
} = useJobHistory({
  kind: props.kind,
  resume: (kind, id) => props.jobs.resume(kind, id),
  opened: item => emit('opened', item),
})
onMounted(loadOwners)
onUnmounted(dispose)
</script>

<template>
  <section class="panel" aria-labelledby="job-history-title" :aria-busy="loading">
    <h3 id="job-history-title">{{ preparation ? '论文准备作业历史' : '研究任务的报告历史' }}</h3>
    <p class="hint">从服务器读取历史记录，无需记住作业编号。每页显示 10 条，最新记录排在前面。</p>
    <div class="toolbar">
      <select v-model="ownerId" :aria-label="preparation ? '选择历史作业所属论文' : '选择历史报告所属任务'"
        :disabled="loading || openingId !== null" @change="loadHistory(0)">
        <option value="">{{ preparation ? '选择论文' : '选择研究任务' }}</option>
        <option v-for="owner in owners" :key="owner.id" :value="owner.id">
          {{ preparation ? '论文' : '任务' }} {{ owner.id }} · {{ owner.title }}
        </option>
      </select>
      <button :disabled="loading || openingId !== null || !ownerId" @click="loadHistory(page)">刷新历史</button>
      <button :disabled="loading || openingId !== null" @click="loadOwners">{{ preparation ? '刷新论文' : '刷新任务' }}</button>
    </div>
    <p v-if="loading" class="notice loading" role="status">正在读取…</p>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="!ownerId && !loading" class="muted small">{{ preparation ? '请选择一篇论文查看准备历史。' : '请选择一个研究任务查看报告历史。' }}</p>
    <article v-for="job in history" :key="job.id" class="job-card history-card">
      <JobSummary :item="{ kind, jobId: job.id, job, error: job.error_message || '' }" />
      <p v-if="preparation" class="muted small">PDF 记录：{{ job.document_id }} · 对应这次作业处理的文件</p>
      <p class="muted small">提交时间：{{ dateText(job.created_at) }}</p>
      <button :disabled="loading || openingId !== null" @click="openJob(job)">
        {{ openingId === job.id ? '载入中…' : '查看并跟踪' }}
      </button>
    </article>
    <p v-if="loaded && history.length === 0" class="empty">{{ preparation ? '这篇论文还没有准备作业。' : '这个研究任务还没有报告作业。' }}</p>
    <div v-if="loaded && history.length" class="actions" aria-label="作业历史分页">
      <button :disabled="loading || openingId !== null || page === 0" @click="loadHistory(page - 1)">上一页</button>
      <span class="muted small" aria-live="polite">第 {{ page + 1 }} 页</span>
      <button :disabled="loading || openingId !== null || !hasMore" @click="loadHistory(page + 1)">下一页</button>
    </div>
  </section>
</template>

<style scoped>
.history-card { border-top: 1px solid var(--line); padding-top: 12px; }
</style>
