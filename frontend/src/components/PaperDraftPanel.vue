<script setup>
import { computed } from 'vue'
import SourcesPanel from './SourcesPanel.vue'
const props = defineProps({ draft: { type: Object, required: true } })
const emit = defineEmits(['source'])
const sections = [['introduction', '1 引言'], ['related_work', '2 相关工作'], ['methods', '3 方法'], ['experiments', '4 实验设置与结果'], ['discussion', '5 讨论与局限'], ['conclusion', '6 结论']]
// 与后端导出一致：同一篇论文只分配一个文献编号。
const references = computed(() => [...new Map(props.draft.evidence.map(source => [source.paper_id, source])).values()])
const paperNumber = paperId => references.value.findIndex(source => source.paper_id === paperId) + 1
function paragraphSources(paragraph) {
  const sources = paragraph.source_numbers.map(number => props.draft.evidence.find(source => source.source_number === number)).filter(Boolean)
  return [...new Map(sources.map(source => [source.paper_id, source])).values()]
}
</script>
<template><article class="paper-preview">
  <p class="hint">辅助生成初稿 · 实验导入 {{ draft.import_id }}。正文和引用支持关系仍需核对。</p>
  <h2>{{ draft.content.title }}</h2><section><h3>摘要</h3><p class="prose">{{ draft.content.abstract }}</p></section>
  <section v-for="[field, title] in sections" :key="field"><h3>{{ title }}</h3>
    <div v-for="(paragraph, index) in draft.content[field]" :key="index" class="draft-paragraph"><p class="prose">{{ paragraph.text }}<button v-for="source in paragraphSources(paragraph)" :key="source.paper_id" class="citation-button" :aria-label="`查看参考文献${paperNumber(source.paper_id)}的原文`" @click="emit('source', source)">[{{ paperNumber(source.paper_id) }}]</button></p></div>
  </section>
  <section><h3>参考文献</h3><ol v-if="references.length" class="text-list"><li v-for="source in references" :key="source.paper_id">{{ source.paper_title }} <small>论文 {{ source.paper_id }}；完整条目请核对资料库元数据。</small></li></ol><p v-else class="notice warning">本初稿未登记文献引用，需要核对补充。</p></section>
  <section v-if="draft.missing_information.length"><h3>待补充信息</h3><ul class="text-list"><li v-for="(item, index) in draft.missing_information" :key="index">{{ item }}</li></ul></section>
  <details><summary>查看实际引用的全部原文证据</summary><SourcesPanel :sources="draft.evidence" @source="emit('source', $event)" /></details>
</article></template>
