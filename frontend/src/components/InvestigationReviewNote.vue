<script setup>
defineProps({ review: Object })
const labels = {
  agrees: '复审核对了调查意见',
  disagrees: '复审与调查意见存在分歧',
  uncertain: '调查疑问尚未确认',
}
</script>

<template>
  <div v-if="review?.investigation_assessment" class="notice"
    :class="{ warning: review.investigation_assessment !== 'agrees' }">
    <strong>{{ labels[review.investigation_assessment] }}</strong>
    <p class="prose">{{ review.investigation_reason }}</p>
    <small v-if="review.investigation_assessment !== 'agrees'">请对照原文核对分歧；采用结果不会自动消除分歧。</small>
  </div>
</template>

<style scoped>
p { margin: 8px 0 0; }
small { display: block; margin-top: 8px; }
</style>
