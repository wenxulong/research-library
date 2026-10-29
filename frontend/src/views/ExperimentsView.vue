<script setup>
import { onMounted } from 'vue'
import ExperimentScope from '../components/ExperimentScope.vue'
import ExperimentStatisticsPanel from '../components/ExperimentStatisticsPanel.vue'
import ExperimentAnalysisPanel from '../components/ExperimentAnalysisPanel.vue'
const props = defineProps({ workspace: { type: Object, required: true } })
defineEmits(['writing'])
const { planSetId, uploadPlan, file, importIdInput, imported, dataset, scenario, metrics, statistics, analysis,
  busy, error, message, columns, previewRows, loadPlan, loadImport, initialize, chooseFile, upload, loadStatistics, analyze, exportReport } = props.workspace
onMounted(initialize)
</script>
<template>
  <header class="page-head"><div><h1>实验结果</h1></div></header>
  <p v-if="busy" class="notice loading" role="status">{{ ['analysis', 'report-export', 'draft', 'draft-export'].includes(busy) ? '模型正在处理，请保持页面打开；这会产生API用量。' : '正在处理…' }}</p>
  <p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="message" class="notice success" role="status">{{ message }}</p>
  <section class="panel workflow-section"><h2>1. 上传或打开实验结果</h2>
    <fieldset :disabled="!!busy">
      <form class="toolbar" @submit.prevent="loadPlan()"><label>问题集编号<input v-model="planSetId" required type="number" min="1" step="1" @input="uploadPlan = null" /></label><button type="submit">载入研究方案</button></form>
      <template v-if="uploadPlan"><p class="prose small"><strong>上传至问题集 {{ uploadPlan.question_set_id }}：</strong>{{ uploadPlan.research_question }}</p>
        <p class="hint">CSV需包含以下列，使用UTF-8编码；额外列可以保留。</p><pre class="page-text">{{ uploadPlan.content.result_columns.join(',') }}</pre>
        <form class="toolbar" @submit.prevent="upload"><label class="upload-label">选择 CSV<input type="file" accept=".csv,text/csv" @change="chooseFile" /></label><span class="small">{{ file?.name || '尚未选择文件' }}</span><button class="primary" type="submit" :disabled="!file">上传实验结果</button></form>
      </template>
      <form class="toolbar" @submit.prevent="loadImport()"><label>已有实验导入编号<input v-model="importIdInput" required type="number" min="1" step="1" /></label><button type="submit">打开实验记录</button></form>
    </fieldset>
    <p class="hint">问题集编号和实验导入编号不同。每次上传会新增一条记录，包含当时的计划快照。</p>
  </section>
  <template v-if="imported">
    <section class="panel workflow-section"><div class="row between"><h2>实验导入 {{ imported.id }}</h2><span class="badge">{{ imported.rows.length }} 条记录</span></div>
      <p class="small muted">{{ imported.filename }} · 问题集 {{ imported.question_set_id }}</p><p class="prose small">{{ imported.plan_snapshot.research_question }}</p>
      <details><summary>预览CSV（前20条）</summary><div class="table-scroll"><table class="data-table"><thead><tr><th v-for="column in columns" :key="column">{{ column }}</th></tr></thead><tbody><tr v-for="(row, index) in previewRows" :key="index"><td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td></tr></tbody></table></div></details>
    </section>
    <section class="panel workflow-section"><h2>2. 选择分析范围</h2><ExperimentScope :workspace="workspace" />
      <fieldset :disabled="!!busy"><div class="actions"><button :disabled="!metrics.length || !dataset || !scenario" @click="loadStatistics">查看统计与图表</button><button class="primary" :disabled="!metrics.length || !dataset || !scenario" @click="analyze">生成实验分析</button></div></fieldset>
      <ExperimentStatisticsPanel :statistics="statistics" :import-id="imported.id" :dataset="dataset" :scenario="scenario" />
      <ExperimentAnalysisPanel v-if="analysis" :analysis="analysis" />
    </section>
    <section class="panel workflow-section"><h2>3. 导出研究报告，继续写作</h2><p class="hint">生成并下载报告会重新调用模型生成分析；压缩包包含Markdown、CSV、统计分析、计划和图片。</p>
      <div class="actions"><button :disabled="!!busy" @click="$emit('writing')">进入论文写作 →</button><button class="primary" :disabled="!!busy || !metrics.length || !dataset || !scenario" @click="exportReport">生成并下载研究报告 ZIP</button></div>
    </section>
  </template>
</template>
