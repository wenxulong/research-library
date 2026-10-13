<script setup>
import { computed, ref, watch } from 'vue'
import BaseDialog from './BaseDialog.vue'
import { papersApi } from '../services/api'
const props = defineProps({ source: Object })
defineEmits(['close'])
const loading=ref(false), error=ref(''), pages=ref([]), chunks=ref([]), pageNumber=ref(1)
let sequence=0
watch(() => props.source, async (source) => {
  const token=++sequence; pages.value=[]; chunks.value=[]; error.value=''
  if (!source) return
  loading.value=true; pageNumber.value=source.page_number || 1
  try { const [p,c]=await Promise.all([papersApi.pages(source.paper_id),papersApi.chunks(source.paper_id)]); if (token===sequence) { pages.value=p; chunks.value=c } }
  catch (cause) { if (token===sequence) error.value=cause.message }
  finally { if (token===sequence) loading.value=false }
})
const selectedPage=computed(() => pages.value.find(x => x.page_number === pageNumber.value))
const quotedChunk=computed(() => chunks.value.find(x => x.id === props.source?.chunk_id))
</script>
<template><BaseDialog :open="!!source" :title="source?.paper_title || '论文原文'" @close="$emit('close')"><p v-if="loading" class="empty">正在读取原文…</p><p v-else-if="error" class="notice error">{{ error }}</p><template v-else><div v-if="source?.chunk_id" class="quote-box"><h3>引用文本块 {{ source.chunk_id }}</h3><p v-if="quotedChunk" class="prose">{{ quotedChunk.text }}</p><p v-else class="notice warning">当前PDF中已找不到这个文本块。原资料可能发生变化，请重新分析后核对。</p></div><label class="inline-label">页码<select v-model.number="pageNumber"><option v-for="page in pages" :key="page.id" :value="page.page_number">第 {{ page.page_number }} 页</option></select></label><pre v-if="selectedPage" class="page-text">{{ selectedPage.text }}</pre><p v-else class="empty">尚未保存这一页的正文，请先完成论文准备。</p></template></BaseDialog></template>
