<script setup>
import { onMounted } from 'vue'
import { useResearchPlanning } from '../composables/useResearchPlanning.js'
import { verdicts, dateText } from '../services/labels.js'
import SourcesPanel from '../components/SourcesPanel.vue'
import ResearchPlanPanel from '../components/ResearchPlanPanel.vue'

defineEmits(['source', 'experiments'])
const {
  tasks, taskId, reports, reportId, questionSet, plan, setIdInput, candidateNumber, questionText,
  conditions, busy, error, message, selectionDirty, selectionSaved,
  loadTasks, loadReports, loadSet, initialize, generateCandidates, chooseCandidate, saveSelection, generatePlan,
} = useResearchPlanning()
const conditionFields = [
  ['equipment', '设备条件', '例如：Mac、可用GPU和内存情况'],
  ['data_resources', '数据资源', '已有数据、待获取的数据及标注情况'],
  ['budget', '预算', '可承担的API、算力或数据费用'],
  ['time_budget', '时间安排', '例如：四周，每周可投入的时间'],
  ['existing_implementation', '已有实现（选填）', '已经完成的代码、方法或实验'],
]
onMounted(initialize)
</script>

<template>
  <header class="page-head"><div><p class="eyebrow">研究问题 / RESEARCH PLANNING</p>
    <h1>把文献线索变成研究方案</h1><p class="muted">提出问题、确认方向，再根据实际条件安排实验。</p>
  </div></header>

  <p v-if="busy" class="notice loading" role="status">{{ ['questions', 'plan'].includes(busy) ? '正在调用模型生成，请保持页面打开。' : '正在处理…' }}</p>
  <p v-if="error" class="notice error" role="alert">{{ error }}</p>
  <p v-if="message" class="notice success" role="status">{{ message }}</p>

  <section class="panel planning-section">
    <h2>1. 从对比报告提出问题</h2>
    <p class="hint">使用已完成且审校通过的报告。每次生成会新建一个问题集，已有问题集不会被覆盖。</p>
    <fieldset :disabled="!!busy">
      <div class="form-grid">
        <label>研究任务<select v-model="taskId" @change="loadReports"><option value="">选择研究任务</option>
          <option v-for="task in tasks" :key="task.id" :value="task.id">任务 {{ task.id }} · {{ task.title }}</option>
        </select></label>
        <label>对比报告<select v-model="reportId">
          <option value="">选择报告</option>
          <option v-if="reportId && !reports.some(report => report.id === Number(reportId))" :value="reportId">已保存的来源报告 {{ reportId }}</option>
          <option v-for="report in reports" :key="report.id" :value="report.id" :disabled="report.status !== 'succeeded'">报告 {{ report.id }} · {{ report.status === 'succeeded' ? '已完成' : '未完成' }} · {{ dateText(report.created_at) }}</option>
        </select></label>
      </div>
      <div class="row between"><span class="hint">每个任务显示最近20份报告，生成前会核对审校结果。</span>
        <div class="actions"><button @click="loadTasks">刷新任务</button><button class="primary" :disabled="!reportId" @click="generateCandidates">{{ busy === 'questions' ? '生成中…' : '生成候选问题' }}</button></div>
      </div>
      <form class="toolbar reopen-set" @submit.prevent="loadSet()">
        <label>继续已有问题集<input v-model="setIdInput" type="number" min="1" step="1" required placeholder="问题集编号" /></label>
        <button type="submit">打开问题集</button>
      </form>
    </fieldset>
  </section>

  <section v-if="questionSet" class="panel planning-section">
    <div class="row between"><h2>2. 选择并确认研究问题</h2><span class="badge">问题集 {{ questionSet.id }}</span></div>
    <p class="hint">研究任务 {{ questionSet.task_id }} · 来源报告 {{ questionSet.report_job_id }}。本浏览器会记住最近打开的问题集编号。</p>
    <fieldset :disabled="!!busy">
      <article v-for="(candidate, index) in questionSet.candidates" :key="index" class="candidate-card" :class="{ chosen: candidateNumber === index + 1 }">
        <label class="candidate-choice"><input type="radio" name="research-candidate" :checked="candidateNumber === index + 1" @change="chooseCandidate(index + 1)" />
          <span>候选 {{ index + 1 }} · {{ candidate.research_question }}</span>
        </label>
        <p class="prose small"><strong>文献观察：</strong>{{ candidate.observation }}</p>
        <p class="prose small"><strong>研究价值：</strong>{{ candidate.research_value }}</p>
        <p class="prose small"><strong>验证思路：</strong>{{ candidate.verification_plan }}</p>
        <div class="candidate-evidence">
          <span class="badge" :class="candidate.evidence_review?.verdict === 'supported' ? 'succeeded' : 'unknown'">{{ verdicts[candidate.evidence_review?.verdict] || '尚无审校结果' }}</span>
          <span class="muted small">该判断针对文献观察，不代表研究创新性已得到证明。</span>
        </div>
        <p v-if="candidate.evidence_review" class="notes">{{ candidate.evidence_review.reason }}</p>
        <ul v-if="candidate.uncertainties?.length" class="text-list"><li v-for="(item, i) in candidate.uncertainties" :key="i">待确认：{{ item }}</li></ul>
        <div class="row"><button v-for="number in candidate.source_numbers" :key="number" class="text-button"
          @click="$emit('source', questionSet.sources.find(source => source.source_number === number))">查看资料 {{ number }}</button></div>
      </article>
      <form @submit.prevent="saveSelection">
        <label>最终研究问题<textarea v-model="questionText" rows="4" required maxlength="2000" :disabled="!candidateNumber" placeholder="先选择候选问题，然后可按实际研究方向修改表述。" /></label>
        <div class="row between"><p class="hint">修改并保存问题后，原有研究方案会清除，需要重新生成。</p>
          <button class="primary" type="submit" :disabled="!candidateNumber">保存研究问题</button>
        </div>
      </form>
    </fieldset>
    <details><summary>查看本问题集的全部资料来源</summary><SourcesPanel :sources="questionSet.sources" @source="$emit('source', $event)" /></details>
  </section>

  <section v-if="questionSet" class="panel planning-section">
    <h2>3. 按实际条件制定方案</h2>
    <p v-if="!selectionSaved" class="notice warning">请先保存最终研究问题，再生成方案。</p>
    <p v-else class="prose small"><strong>已确认问题：</strong>{{ questionSet.selected_question }}</p>
    <form @submit.prevent="generatePlan">
      <fieldset :disabled="!!busy || !selectionSaved">
        <div class="form-grid"><label v-for="[field, title, placeholder] in conditionFields" :key="field">{{ title }}
          <textarea v-model="conditions[field]" rows="3" :placeholder="placeholder" :required="field !== 'existing_implementation'" />
        </label></div>
        <div class="actions"><button class="primary" type="submit">{{ busy === 'plan' ? '生成中…' : plan ? '重新生成并保存方案' : '生成并保存方案' }}</button></div>
      </fieldset>
    </form>
    <p class="hint">生成的是待执行计划，实际实验需要你运行。已保存的方案可以通过问题集编号重新打开。</p>
    <ResearchPlanPanel v-if="plan && !selectionDirty" :plan="plan" />
    <div v-if="plan && !selectionDirty" class="actions"><button class="primary" :disabled="!!busy" @click="$emit('experiments', questionSet.id)">上传实验结果 →</button></div>
  </section>
</template>

<style scoped>
.planning-section { margin-bottom: 22px; }
.planning-section h2 { margin-bottom: 12px; }
.candidate-card { border: 1px solid var(--line); border-radius: 9px; padding: 18px; margin: 16px 0; }
.candidate-card.chosen { border-color: var(--accent); background: #f6faf7; }
.candidate-choice { display: flex; align-items: flex-start; gap: 10px; line-height: 1.8; font-size: 15px; margin-bottom: 14px; }
.candidate-choice input { width: auto; flex-shrink: 0; margin-top: 7px; accent-color: var(--accent); }
.candidate-card p { margin-bottom: 10px; }
.candidate-evidence { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 12px 0; }
.reopen-set { border-top: 1px solid var(--line); padding-top: 18px; align-items: flex-end; }
.reopen-set label { margin: 0; }
.reopen-set input { max-width: 200px; }
</style>
