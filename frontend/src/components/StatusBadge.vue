<script setup>
defineProps({ status: String })
const names = { queued:'排队中', running:'处理中', succeeded:'已完成', failed:'失败', pending:'待处理', completed:'已完成', loading:'查询中', ok:'正常', error:'异常', unknown:'未确认', degraded:'需要检查' }
</script>
<template><span class="badge" :class="status">{{ names[status] || status || '未开始' }}</span></template>
