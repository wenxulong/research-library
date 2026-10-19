<script setup>
const props = defineProps({ workspace: { type: Object, required: true } })
const { dataset, scenario, datasets, scenarios, metrics, metricOptions, busy, changeDataset } = props.workspace
</script>
<template>
  <fieldset :disabled="!!busy">
    <div class="form-grid">
      <label>数据集<select v-model="dataset" @change="changeDataset"><option value="">选择数据集</option><option v-for="item in datasets" :key="item" :value="item">{{ item }}</option></select></label>
      <label>场景<select v-model="scenario"><option value="">选择场景</option><option v-for="item in scenarios" :key="item" :value="item">{{ item }}</option></select></label>
    </div>
    <p class="small"><strong>分析指标（可多选）</strong></p>
    <div class="metric-choices"><label v-for="name in metricOptions" :key="name"><input v-model="metrics" type="checkbox" :value="name" />{{ name }}</label></div>
    <p v-if="!metricOptions.length" class="notice warning">没有找到可选指标列，请核对CSV是否包含数值指标。</p>
    <p class="hint">不同数据集和场景分别比较；修改范围后，旧统计、分析和初稿会从页面清除。</p>
  </fieldset>
</template>
