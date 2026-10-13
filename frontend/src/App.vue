<script setup>
import { onUnmounted, ref } from 'vue'
import { useJobs } from './composables/useJobs'
import PapersView from './views/PapersView.vue'
import TasksView from './views/TasksView.vue'
import JobsView from './views/JobsView.vue'
import HealthView from './views/HealthView.vue'
import EvidenceDrawer from './components/EvidenceDrawer.vue'
const view=ref('papers'),source=ref(null),paperRevision=ref(0),toasts=ref([])
const timers=new Set();let toastId=0
function notify(message,type='success'){const id=++toastId;toasts.value.push({id,message,type});const timer=setTimeout(()=>{toasts.value=toasts.value.filter(x=>x.id!==id);timers.delete(timer)},9000);timers.add(timer)}
const jobs=useJobs(notify)
onUnmounted(()=>{for(const timer of timers)clearTimeout(timer)})
const navigation=[{id:'papers',title:'论文资料库',icon:'▤',hint:'资料上传与准备'},{id:'tasks',title:'研究任务',icon:'◎',hint:'问题、分析与证据'},{id:'jobs',title:'作业与报告',icon:'◷',hint:'后台进度与成果'},{id:'health',title:'服务状态',icon:'◇',hint:'运行环境检查'}]
</script>
<template><div class="app-shell"><aside class="sidebar"><a href="#" class="brand" @click.prevent="view='papers'"><span class="brand-mark">研</span><span>科研资料库<small>RESEARCH WORKSPACE</small></span></a><p class="sidebar-caption">从资料到证据，让研究有迹可循。</p><nav aria-label="主导航"><button v-for="item in navigation" :key="item.id" :class="{selected:view===item.id}" @click="view=item.id"><span class="nav-icon" aria-hidden="true">{{item.icon}}</span><span><strong>{{item.title}}</strong><small>{{item.hint}}</small></span><span v-if="item.id==='jobs'&&jobs.activeCount.value" class="nav-count">{{jobs.activeCount.value}}</span></button></nav><div class="sidebar-note"><span class="status-dot"></span>个人科研工作台<p>准备资料 → 分析证据 → 形成报告</p></div></aside><main class="main-content"><section v-show="view==='papers'"><PapersView :jobs="jobs" :notify="notify" @source="source=$event" @changed="paperRevision++" /></section><section v-show="view==='tasks'"><TasksView :jobs="jobs" :notify="notify" :paper-revision="paperRevision" @source="source=$event" @jobs="view='jobs'" /></section><section v-show="view==='jobs'"><JobsView :jobs="jobs" :notify="notify" @source="source=$event" /></section><section v-show="view==='health'"><HealthView /></section></main></div><EvidenceDrawer :source="source" @close="source=null" /><div class="toast-stack" aria-live="polite"><div v-for="toast in toasts" :key="toast.id" class="toast" :class="toast.type"><span>{{toast.message}}</span><button aria-label="关闭提示" @click="toasts=toasts.filter(x=>x.id!==toast.id)">×</button></div></div></template>
