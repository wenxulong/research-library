<script setup>
import { ref, watch } from 'vue'
import { experimentsApi } from '../services/api.js'
const props = defineProps({ statistics: { type: Array, default: () => [] }, importId: Number, dataset: String, scenario: String })
const failedImages = ref(new Set())
watch(() => props.statistics, () => { failedImages.value = new Set() })
const numberText = value => value == null ? '—' : new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 6 }).format(value)
</script>
<template>
  <article v-for="item in statistics" :key="item.metric" class="statistic-section">
    <h3>{{ item.metric }}</h3>
    <p v-if="!item.status_available" class="notice warning">CSV没有status列，不能判断哪些运行成功或失败。</p>
    <div class="table-scroll"><table class="data-table"><thead><tr><th>方法</th><th>均值</th><th>样本标准差</th><th>有效 / 总数</th><th>失败</th><th>缺失</th><th>无效</th><th>未知状态</th></tr></thead>
      <tbody><tr v-for="group in item.groups" :key="group.method"><td>{{ group.method }}</td><td>{{ numberText(group.mean) }}</td><td>{{ numberText(group.stddev) }}</td><td>{{ group.valid_count }} / {{ group.total_count }}</td><td>{{ group.failed_count }}</td><td>{{ group.missing_count }}</td><td>{{ group.invalid_count }}</td><td>{{ group.unknown_status_count }}</td></tr></tbody>
    </table></div>
    <p class="hint">“—”表示无法计算。标准差不是置信区间；失败、缺失和无效值不会作为零计入均值。</p>
    <template v-if="item.groups.some(group => group.mean !== null)">
      <p v-if="failedImages.has(item.metric)" class="notice error">图表暂时无法读取，可重新点击“查看统计与图表”。</p>
      <img v-else class="experiment-chart" :src="experimentsApi.chartUrl(importId, item.metric, dataset, scenario)" :alt="`${item.metric}在${dataset}、${scenario}下的方法对比图`" @error="failedImages.add(item.metric)" />
      <a class="button-link" :href="experimentsApi.chartUrl(importId, item.metric, dataset, scenario)" download>下载此图 PNG</a>
    </template>
    <p v-else class="notice warning">当前范围没有有效数值，无法绘制此指标。</p>
  </article>
</template>
