<script setup>
defineProps({ plan: { type: Object, required: true } })
const sections = [
  ['method_steps', '方法步骤'], ['baselines', '对比基线'], ['metrics', '指标定义'],
  ['main_experiments', '主实验'], ['ablation_experiments', '消融实验'],
  ['execution_steps', '执行顺序'], ['risks', '待确认事项与风险'],
]
</script>

<template>
  <div class="analysis">
    <p class="notice success">方案已保存 · 问题集 {{ plan.question_set_id }} · 研究任务 {{ plan.task_id }}</p>
    <section><h3>研究问题</h3><p class="prose">{{ plan.research_question }}</p></section>
    <section><h3>研究目标</h3><p class="prose">{{ plan.content.objective }}</p></section>
    <section><h3>待验证假设</h3><p class="prose">{{ plan.content.hypothesis }}</p></section>
    <section><h3>数据准备</h3><p class="prose">{{ plan.content.data_plan }}</p></section>
    <section v-for="[field, title] in sections" :key="field">
      <h3>{{ title }}</h3>
      <ol v-if="plan.content[field]?.length" class="text-list"><li v-for="(item, index) in plan.content[field]" :key="index">{{ item }}</li></ol>
      <p v-else class="muted small">本方案未安排这一项。</p>
    </section>
    <section><h3>实验结果 CSV 列名</h3><p class="hint">后续导入实验结果时，请保留以下列名；额外列也可以保留。</p>
      <pre class="page-text">{{ plan.content.result_columns.join(',') }}</pre>
    </section>
  </div>
</template>
