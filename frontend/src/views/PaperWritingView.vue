<script setup>
import ExperimentScope from '../components/ExperimentScope.vue'
import PaperDraftPanel from '../components/PaperDraftPanel.vue'
const props = defineProps({ workspace: { type: Object, required: true } })
defineEmits(['source', 'experiments'])
const { imported, importIdInput, draftIdInput, metrics, dataset, scenario, writing, draft, expansion, expansionSuggestion, skillInstruction, skillQuestion, busy, error, message, loadImport, loadDraft, generateDraft, exportDraft, expandDraft, discardExpansion, acceptExpansion, runDraftSkill } = props.workspace
const sections = [['introduction', '引言'], ['related_work', '相关工作'], ['methods', '方法'], ['experiments', '实验设置与结果'], ['discussion', '讨论与局限'], ['conclusion', '结论']]
</script>
<template>
  <header class="page-head"><div><p class="eyebrow">论文初稿 / WRITING</p><h1>让研究材料成为论文初稿</h1><p class="muted">补充实际方法与实验条件，保留引用和待完善内容。</p></div></header>
  <p v-if="busy" class="notice loading" role="status">{{ busy === 'draft' ? '正在生成并保存初稿，请保持页面打开；生成会产生API用量。' : busy === 'draft-skill' ? '正在理解扩写要求并执行章节扩写 Skill，会产生API用量；原稿尚未修改。' : busy === 'draft-expand' ? '正在生成章节扩写建议，会产生API用量；原稿尚未修改。' : '正在处理…读取、保存和下载已有内容不会调用模型。' }}</p>
  <p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="message" class="notice success" role="status">{{ message }}</p>
  <section class="panel workflow-section"><h2>1. 使用已导入的实验结果</h2><fieldset :disabled="!!busy"><form class="toolbar" @submit.prevent="loadImport()"><label>实验导入编号<input v-model="importIdInput" required type="number" min="1" step="1" /></label><button type="submit">打开实验记录</button><button type="button" @click="$emit('experiments')">前往实验结果页面</button></form></fieldset>
    <fieldset :disabled="!!busy"><form class="toolbar" @submit.prevent="loadDraft()"><label>已保存初稿编号<input v-model="draftIdInput" required type="number" min="1" step="1" /></label><button type="submit">打开已保存初稿</button></form></fieldset>
    <p v-if="imported" class="prose small"><strong>当前使用实验导入 {{ imported.id }}：</strong>{{ imported.plan_snapshot.research_question }}</p><p v-else class="empty">请先打开已有实验记录、已保存初稿，或到实验结果页面上传CSV。</p>
  </section>
  <template v-if="imported">
    <section class="panel workflow-section"><h2>2. 确认写作范围和实际研究情况</h2><ExperimentScope :workspace="workspace" />
      <form @submit.prevent="generateDraft"><fieldset :disabled="!!busy">
        <label>实际方法与实现<textarea v-model="writing.implementation_details" rows="5" placeholder="实际实现了哪些步骤、使用哪些参数，哪些计划尚未实现。" /></label>
        <label>实际实验设置<textarea v-model="writing.actual_experiment_setup" rows="5" placeholder="实际设备、数据划分、运行次数、随机种子、模型版本和指标单位。" /></label>
        <p class="hint">这两项可以暂时留空，初稿会标记待补充。研究计划不会自动当作已完成的工作。</p>
        <div class="actions"><button class="primary" type="submit" :disabled="!metrics.length || !dataset || !scenario">{{ draft ? '重新生成并保存新初稿' : '生成并保存初稿' }}</button><button type="button" :disabled="!draft?.id" @click="exportDraft">下载当前已保存初稿 ZIP</button></div>
      </fieldset></form>
      <p class="hint">下载使用当前预览的已保存正文和材料，不重新生成。修改范围或实际研究说明会清除当前预览，但不会删除已保存记录，可按编号重新打开。</p>
    </section>
    <section v-if="draft?.id" class="panel workflow-section">
      <h2>3. 扩写某一章</h2>
      <p class="hint">模型先生成建议，你确认后才保存。可以先修改建议的文字，再确认替换整章。</p>
      <form @submit.prevent="runDraftSkill"><fieldset :disabled="!!busy">
        <label>一句话扩写（Skill）<textarea v-model="skillInstruction" required maxlength="2000" rows="3" placeholder="例如：帮我扩写引言，补充研究背景和动机，保留原文依据。" /></label>
        <p class="hint">系统识别要扩写的章节；要求不明确时会先询问。执行扩写通常需要两次模型调用，比手动选择章节多一次识别。</p>
        <button class="primary" type="submit" :disabled="!skillInstruction.trim()">执行写作 Skill</button>
      </fieldset></form>
      <p v-if="skillQuestion" class="notice warning" role="status">{{ skillQuestion }} 请在上方补全要求后重新执行。</p>
      <h3>或手动选择章节</h3>
      <form @submit.prevent="expandDraft"><fieldset :disabled="!!busy">
        <label>选择章节<select v-model="expansion.section"><option v-for="[field, title] in sections" :key="field" :value="field">{{ title }}</option></select></label>
        <label>扩写要求<textarea v-model="expansion.instruction" required maxlength="2000" rows="3" placeholder="例如：补充背景与研究动机，展开为4个段落，保留已有依据，不编造研究贡献。" /></label>
        <button class="primary" type="submit" :disabled="!expansion.instruction.trim()">生成扩写建议</button>
      </fieldset></form>
      <div v-if="expansionSuggestion" class="workflow-section">
        <h3>{{ sections.find(([field]) => field === expansionSuggestion.section)?.[1] }}：扩写建议</h3>
        <p class="notice warning">尚未保存。确认后会替换本章全部段落，其他章节保持原样。</p>
        <fieldset :disabled="!!busy">
          <label v-for="(paragraph, index) in expansionSuggestion.paragraphs" :key="index">第 {{ index + 1 }} 段
            <textarea v-model="paragraph.text" rows="5" />
            <span class="hint">{{ paragraph.source_numbers.length ? `引用资料编号：${paragraph.source_numbers.join('、')}` : '未引用文献资料' }}</span>
          </label>
          <p class="hint">这里显示的是原文证据的资料编号，不是最终参考文献序号。请核对文字与引用的支持关系。</p>
          <div class="actions"><button class="primary" type="button" :disabled="expansionSuggestion.paragraphs.some(paragraph => !paragraph.text.trim())" @click="acceptExpansion">确认替换本章并保存</button><button type="button" @click="discardExpansion">放弃建议</button></div>
        </fieldset>
      </div>
    </section>
    <section v-if="draft" class="panel workflow-section"><p class="notice success">已保存初稿 {{ draft.id }} · 创建时间 {{ draft.created_at?.replace('T', ' ') }}</p><PaperDraftPanel :draft="draft" @source="$emit('source', $event)" /></section>
  </template>
</template>
