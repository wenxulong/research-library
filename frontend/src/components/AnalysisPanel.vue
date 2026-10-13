<script setup>
import { fields } from '../services/labels'
import SourcesPanel from './SourcesPanel.vue'
defineProps({ analysis: Object })
defineEmits(['source'])
</script>
<template><div v-if="analysis" class="analysis"><section><h3>研究概述</h3><p class="prose">{{ analysis.summary }}</p></section><section v-for="field in ['methods','experiments','conclusions','limitations','evidence_notes']" :key="field"><h3>{{ fields[field] }}</h3><ul v-if="analysis[field]?.length" class="text-list"><li v-for="(text,index) in analysis[field]" :key="index">{{ text }}</li></ul><p v-else class="muted">暂无内容</p></section><SourcesPanel :sources="analysis.sources" @source="$emit('source',$event)" /></div></template>
