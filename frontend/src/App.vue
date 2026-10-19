<script setup>
import { onUnmounted, ref } from 'vue'
import { useJobs } from './composables/useJobs'
import PapersView from './views/PapersView.vue'
import TasksView from './views/TasksView.vue'
import JobsView from './views/JobsView.vue'
import HealthView from './views/HealthView.vue'
import ResearchPlanningView from './views/ResearchPlanningView.vue'
import ExperimentsView from './views/ExperimentsView.vue'
import PaperWritingView from './views/PaperWritingView.vue'
import { createExperimentWorkspace } from './composables/useExperimentWorkspace.js'
import EvidenceDrawer from './components/EvidenceDrawer.vue'
const view=ref('papers'),source=ref(null),paperRevision=ref(0),toasts=ref([])
const timers=new Set();let toastId=0
function notify(message,type='success'){const id=++toastId;toasts.value.push({id,message,type});const timer=setTimeout(()=>{toasts.value=toasts.value.filter(x=>x.id!==id);timers.delete(timer)},9000);timers.add(timer)}
const jobs=useJobs(notify)
const experiments=createExperimentWorkspace()
function startExperiments(setId){if(experiments.busy.value){notify('请先等待当前实验或写作操作完成。','error');return}view.value='experiments';experiments.loadPlan(setId)}
onUnmounted(()=>{for(const timer of timers)clearTimeout(timer)})
const navigation=[{id:'papers',title:'论文资料库',icon:'▤',hint:'资料上传与准备'},{id:'tasks',title:'研究任务',icon:'◎',hint:'问题、分析与证据'},{id:'jobs',title:'作业与报告',icon:'◷',hint:'后台进度与成果'},{id:'planning',title:'研究方案',icon:'↗',hint:'选问题与实验计划'},{id:'experiments',title:'实验结果',icon:'▥',hint:'导入、统计与图表'},{id:'writing',title:'论文写作',icon:'✎',hint:'初稿与引用导出'},{id:'health',title:'服务状态',icon:'◇',hint:'运行环境检查'}]
</script>
<template><div class="app-shell"><aside class="sidebar"><a href="#" class="brand" @click.prevent="view='papers'"><span class="brand-mark">研</span><span>科研资料库<small>RESEARCH WORKSPACE</small></span></a><p class="sidebar-caption">从资料到证据，让研究有迹可循。</p><nav aria-label="主导航"><button v-for="item in navigation" :key="item.id" :class="{selected:view===item.id}" @click="view=item.id"><span class="nav-icon" aria-hidden="true">{{item.icon}}</span><span><strong>{{item.title}}</strong><small>{{item.hint}}</small></span><span v-if="item.id==='jobs'&&jobs.activeCount.value" class="nav-count">{{jobs.activeCount.value}}</span></button></nav><div class="sidebar-note"><span class="status-dot"></span>个人科研工作台<p>准备资料 → 分析证据 → 形成报告</p></div></aside><main class="main-content"><section v-show="view==='papers'"><PapersView :jobs="jobs" :notify="notify" @source="source=$event" @changed="paperRevision++" /></section><section v-show="view==='tasks'"><TasksView :jobs="jobs" :notify="notify" :paper-revision="paperRevision" @source="source=$event" @jobs="view='jobs'" /></section><section v-show="view==='jobs'"><JobsView :jobs="jobs" :notify="notify" @source="source=$event" /></section><section v-show="view==='planning'"><ResearchPlanningView @source="source=$event" @experiments="startExperiments" /></section><section v-show="view==='experiments'"><ExperimentsView :workspace="experiments" @writing="view='writing'" /></section><section v-show="view==='writing'"><PaperWritingView :workspace="experiments" @source="source=$event" @experiments="view='experiments'" /></section><section v-show="view==='health'"><HealthView /></section></main></div><EvidenceDrawer :source="source" @close="source=null" /><div class="toast-stack" aria-live="polite"><div v-for="toast in toasts" :key="toast.id" class="toast" :class="toast.type"><span>{{toast.message}}</span><button aria-label="关闭提示" @click="toasts=toasts.filter(x=>x.id!==toast.id)">×</button></div></div></template>
