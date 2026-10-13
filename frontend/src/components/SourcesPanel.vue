<script setup>
defineProps({ sources: { type: Array, default: () => [] } })
defineEmits(['source'])
</script>
<template><section v-if="sources.length" class="sources"><h3>原文来源 <small>{{ sources.length }} 条</small></h3><button v-for="(source,index) in sources" :key="`${source.paper_id}:${source.chunk_id}:${source.source_number ?? index}`" class="source-row" @click="$emit('source', source)"><span class="source-number">资料 {{ source.source_number ?? index + 1 }}</span><span class="source-title">{{ source.paper_title }}<small>论文 {{ source.paper_id }} · 第 {{ source.page_number }} 页 · 文本块 {{ source.chunk_id }}</small></span><span aria-hidden="true">↗</span></button></section></template>
