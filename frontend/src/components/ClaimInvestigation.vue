<script setup>
import { computed, toRef } from 'vue'
import InvestigationReviewNote from './InvestigationReviewNote.vue'
import { createClaimInvestigation } from '../composables/useClaimInvestigation.js'
import { verdicts, reviewEvidenceLabels } from '../services/labels'

const props = defineProps({ jobId: Number, claimId: String })
const emit = defineEmits(['adopted'])
const { instruction, records, selected, busy, error, message, loadHistory, submit, openRecord, adopt, dismiss } =
  createClaimInvestigation({ jobId: toRef(props, 'jobId'), claimId: toRef(props, 'claimId'), onAdopted: result => emit('adopted', result) })
const result = computed(() => selected.value?.result)
function toggle(event) { if (event.target.open) loadHistory() }
</script>

<template>
  <details class="claim-investigation" @toggle="toggle">
    <summary>继续查证</summary>
    <p class="hint">仅查证当前陈述。每次独立提交会调用 DeepSeek、产生 API 用量，并保存候选记录；不会覆盖原报告。</p>
    <form @submit.prevent="submit">
      <label>本次查证要求
        <textarea v-model="instruction" rows="3" maxlength="2000" required
          placeholder="例如：检查高负载场景，寻找可能的反例。" :disabled="busy" />
      </label>
      <div class="actions">
        <button type="submit" class="primary" :disabled="busy || !instruction.trim()">{{ busy ? '正在处理…' : '开始查证' }}</button>
        <button type="button" :disabled="busy" @click="loadHistory">读取已保存记录</button>
      </div>
    </form>
    <p v-if="busy" class="notice" role="status">正在处理，请勿重复提交。停止等待不会取消服务端查证。</p>
    <p v-if="error" class="notice error" role="alert">{{ error }} 可先读取已保存记录核对结果。</p>
    <p v-if="message" class="notice" role="status">{{ message }}</p>
    <div v-if="records.length" class="actions" aria-label="查证历史">
      <button v-for="record in records" :key="record.id" :disabled="busy"
        @click="openRecord(record.id)">查证 {{ record.id }}</button>
    </div>
    <p v-else-if="!busy" class="hint">暂无已读取的查证记录。这里最多显示最近 10 条。</p>
    <article v-if="result" class="investigation-result">
      <h4>查证 {{ selected.id }} · {{ selected.adopted_at ? '已采用' : '候选未采用' }}</h4>
      <p class="prose"><strong>用户要求：</strong>{{ result.instruction }}</p>
      <p v-if="result.status === 'limit_reached'" class="notice warning">已达到执行上限；以下仅为已取得材料的复审，不代表完成全部查证。</p>
      <p class="prose">{{ result.summary }}</p>
      <p>原审校：{{ verdicts[result.previous_review.verdict] || result.previous_review.verdict }} → 本次复审：{{ verdicts[result.review.verdict] || result.review.verdict }}</p>
      <p class="prose">{{ result.review.reason }}</p>
      <InvestigationReviewNote :review="result.review" />
      <p><strong>{{ reviewEvidenceLabels[result.review.verdict] }}：</strong>{{ result.review.source_numbers.join('、') || '无' }}</p>
      <p class="hint">上述编号是复审核查的资料，具体关系见复审说明；并非每段资料都支持正文。</p>
      <p class="hint">复审仍可能误判，请核对下面原文。未采用的候选资料编号不属于正式报告。</p>
      <details v-for="source in result.evidence" :key="source.chunk_id">
        <summary>候选资料 {{ source.source_number }} · {{ source.paper_title }} · 第 {{ source.page_number }} 页 {{ result.added_chunk_ids.includes(source.chunk_id) ? '（本次补充）' : '（已有）' }}</summary>
        <p v-if="source.duplicate_of_chunk_id" class="hint">正文同文本块 {{ source.duplicate_of_chunk_id }}，保留本资料的编号与页码。</p>
        <p class="hint">文本块 {{ source.chunk_id }}</p><p class="prose">{{ source.text }}</p>
      </details>
      <details><summary>工具执行记录（{{ result.steps.length }} 次）</summary>
        <article v-for="(step, index) in result.steps" :key="index">
          <p>第 {{ step.round }} 轮 · {{ step.tool_name }}</p><pre>{{ step.arguments }}</pre>
          <p v-if="!step.observation.ok" class="notice error">{{ step.observation.error }}</p>
          <p v-else class="hint">新增原文 {{ step.observation.new_evidence_count ?? step.observation.evidence?.length ?? 0 }} 条</p>
          <p v-if="step.observation.evidence?.some(item => item.context_status === 'already_provided')" class="hint">重复文本块仅返回定位信息，原文已在此前提供。</p>
          <p v-if="step.observation.evidence?.some(item => item.context_status === 'duplicate_content')" class="hint">{{ step.observation.duplicate_content_count ?? step.observation.evidence.filter(item => item.context_status === 'duplicate_content').length }} 条同论文重复正文已合并，引用使用已提供原文的主块编号。</p>
          <p v-if="step.observation.evidence?.some(item => item.context_status === 'context_budget_exceeded')" class="hint">部分原文超出本次预算，未发送给模型，不能作为已读证据。</p>
        </article>
      </details>
      <p class="notice">采用会保存证据和复审状态；仅完全支持时补充正文引用。反对证据、适用条件与缺口单独展示，不自动改写事实正文，也不代表审校通过。</p>
      <div class="actions">
        <button class="primary" :disabled="busy || !!selected.adopted_at" @click="adopt">{{ selected.adopted_at ? '已采用' : '采用证据与复审结果' }}</button>
        <button :disabled="busy" @click="dismiss">暂不采用／收起结果</button>
      </div>
    </article>
  </details>
</template>

<style scoped>
.claim-investigation { margin-top: 12px; border-top: 1px solid var(--line); padding: 12px 0; }
.claim-investigation summary { color: var(--accent); cursor: pointer; }
.investigation-result { margin-top: 18px; padding: 14px; background: var(--surface-soft); border-radius: 8px; }
.investigation-result details { margin: 12px 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
</style>
