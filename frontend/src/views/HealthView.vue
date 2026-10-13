<script setup>
import { onMounted, ref } from 'vue'
import { request } from '../services/api'
import StatusBadge from '../components/StatusBadge.vue'
const report=ref(null),error=ref(''),loading=ref(false),updated=ref('')
const labels={database:'数据库与迁移版本',redis:'消息队列',worker:'后台执行器',ollama:'本地向量模型'}
async function refresh(){loading.value=true;error.value='';try{report.value=await request('/health/ready',{acceptedStatuses:[503]});updated.value=new Date().toLocaleTimeString()}catch(cause){error.value=cause.message}finally{loading.value=false}}
onMounted(refresh)
</script>
<template><header class="page-head"><div><p class="eyebrow">运行状态 / SYSTEM</p><h1>开始研究前，确认服务就绪</h1><p class="muted">检查数据库、队列和模型的可用情况。</p></div><button :disabled="loading" @click="refresh">{{loading?'检查中…':'重新检查'}}</button></header><p v-if="error" class="notice error">{{error}}</p><div v-if="report" class="health-grid"><article v-for="(check,key) in report.checks" :key="key" class="panel"><div class="row between"><h3>{{labels[key]||key}}</h3><StatusBadge :status="check.status" /></div><p>{{check.message}}</p><small v-if="check.error_type" class="muted">错误类型：{{check.error_type}}</small></article></div><p v-if="report" class="notice" :class="report.capabilities?.deepseek_configured?'success':'warning'">{{report.capabilities?.deepseek_configured?'DeepSeek 密钥已配置；此检查不会调用远程模型。':'尚未配置 DeepSeek 密钥。论文准备和本地检索仍可使用，生成类功能需要先配置密钥。'}}</p><p class="hint">后台执行器未回复时显示“未确认”，可能正在处理长任务。请结合进度查看，不要据此强行停止作业。</p><p v-if="updated" class="muted small">上次检查：{{updated}}</p></template>
