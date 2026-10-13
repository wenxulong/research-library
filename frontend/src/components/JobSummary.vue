<script setup>
import StatusBadge from './StatusBadge.vue'
import { stages } from '../services/labels'
defineProps({ item: Object })
</script>
<template><div v-if="item" class="job-summary"><div class="row"><StatusBadge :status="item.job?.status" /><span>作业 {{ item.jobId }} · {{ stages[item.job?.stage] || item.job?.stage || '查询中' }}</span></div><p v-if="item.error" class="notice error">{{ item.error }}</p><div v-if="item.kind === 'preparation' && item.job?.result" class="metrics"><span><strong>{{ item.job.result.page_count }}</strong>页正文</span><span><strong>{{ item.job.result.chunk_count }}</strong>个文本块</span><span><strong>{{ item.job.result.indexed_count }}</strong>条新增向量</span></div></div></template>
