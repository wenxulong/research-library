<script setup>
defineProps({ analysis: { type: Object, required: true } })
const sections = [['differences', '方法差异'], ['tradeoffs', '多指标权衡'], ['limitations', '结果限制'], ['next_experiments', '后续实验建议']]
</script>
<template><div class="analysis"><section><h3>总体分析</h3><p class="prose">{{ analysis.content.summary }}</p></section>
  <section v-for="[field, title] in sections" :key="field"><h3>{{ title }}</h3><ul class="text-list"><li v-for="(text, index) in analysis.content[field]" :key="index">{{ text }}</li></ul></section>
</div></template>
