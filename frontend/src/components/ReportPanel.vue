<script setup>
import SourcesPanel from './SourcesPanel.vue'
import InvestigationReviewNote from './InvestigationReviewNote.vue'
import ClaimInvestigation from './ClaimInvestigation.vue'
import { verdicts, reviewEvidenceLabels } from '../services/labels'
const props = defineProps({ result: Object, jobId: Number })
function reviewFor(statement) {
  return props.result?.review?.items?.find(item => statement.claim_id && item.claim_id === statement.claim_id)
}
defineEmits(['source', 'adopted'])
const sections = { method_comparisons:'方法对比', experiment_comparisons:'实验对比', conclusion_comparisons:'结论对比', evidence_notes:'证据备注' }
</script>
<template><div v-if="result" class="report"><div class="report-banner" :class="result.passed ? 'verified' : 'unverified'"><strong>{{ result.passed ? '本轮审校通过' : '待核验报告' }}</strong><p>{{ result.passed ? '查看引用原文，确认适合你的研究用途。' : '作业已完成，但报告仍有未通过审校的陈述，请结合原文核对。' }}</p><small>修正轮次：{{ result.correction_rounds }}</small></div><section v-for="(label,field) in sections" :key="field"><h3>{{ label }}</h3><ul v-if="result.report[field]?.length" class="text-list"><li v-for="(statement,index) in result.report[field]" :key="statement.claim_id || index">{{ statement.text }}<small v-if="statement.source_numbers?.length">正文引用（是否成立以审校为准）：{{ statement.source_numbers.join('、') }}</small><div v-if="reviewFor(statement)" class="review-item">
  <span class="badge" :class="reviewFor(statement).verdict === 'supported' ? 'ok' : 'unknown'">{{ verdicts[reviewFor(statement).verdict] }}</span>
  <p class="prose">{{ reviewFor(statement).reason }}</p>
  <InvestigationReviewNote :review="reviewFor(statement)" />
  <small>{{ reviewEvidenceLabels[reviewFor(statement).verdict] }}：{{ reviewFor(statement).source_numbers?.join('、') || '无' }}</small>
</div><ClaimInvestigation v-if="jobId && statement.claim_id" :key="`${jobId}:${statement.claim_id}`" :job-id="jobId" :claim-id="statement.claim_id" @adopted="$emit('adopted', $event)" /><small v-else class="hint">旧报告无陈述编号，重新生成成功的报告后可继续查证。</small></li></ul><p v-else class="muted">暂无内容</p></section><details><summary>查看最终审校记录（{{ result.review?.items?.length || 0 }} 条）</summary><article v-for="(item,index) in result.review?.items" :key="index" class="review-item"><span class="badge" :class="item.verdict === 'supported' ? 'ok' : 'unknown'">{{ verdicts[item.verdict] || item.verdict }}</span><p class="prose">{{ item.claim }}</p><p class="muted">{{ item.reason }}</p><InvestigationReviewNote :review="item" /></article></details><details v-if="result.rejected_revisions?.length"><summary>未采纳的修订建议（{{ result.rejected_revisions.length }} 条）</summary><article v-for="(item,index) in result.rejected_revisions" :key="index" class="review-item"><p>{{ item.revision.revised_claim }}</p><p class="muted">{{ item.review.reason }}</p></article></details><SourcesPanel :sources="result.report.sources" @source="$emit('source',$event)" /></div></template>
