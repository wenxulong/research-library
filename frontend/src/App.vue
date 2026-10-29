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
const navigation = [
  { id: 'papers', title: '论文资料库', path: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 3H20v19H6.5A2.5 2.5 0 0 1 4 19.5v-14A2.5 2.5 0 0 1 6.5 3Z' },
  { id: 'tasks', title: '研究任务', path: 'M9 5H5v16h14V5h-4M9 3h6v4H9zM8 12h8M8 16h5' },
  { id: 'jobs', title: '作业与报告', path: 'M12 8v4l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0' },
  { id: 'planning', title: '研究方案', path: 'M8 3H4v18h16V8l-5-5H8M14 3v6h6M8 13h8M8 17h5' },
  { id: 'experiments', title: '实验结果', path: 'M4 3v17h17M8 16v-5M13 16V7M18 16v-8' },
  { id: 'writing', title: '论文写作', path: 'M12 20h9M16 3l5 5-12 12-6 1 1-6L16 3ZM13 6l5 5' },
  { id: 'health', title: '服务状态', path: 'M3 12h4l3-8 4 16 3-8h4' },
]
</script>
<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a href="#" class="brand" @click.prevent="view='papers'">
        <span class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h6l2 2 2-2h6v15h-6l-2 2-2-2H4zM12 6v15" /></svg>
        </span>
        <span>科研工作台</span>
      </a>
      <nav aria-label="主导航">
        <button v-for="item in navigation" :key="item.id" :class="{selected:view===item.id}"
          :aria-current="view===item.id ? 'page' : undefined" @click="view=item.id">
          <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="item.path" /></svg>
          <strong>{{ item.title }}</strong>
          <span v-if="item.id==='jobs' && jobs.activeCount.value" class="nav-count">{{ jobs.activeCount.value }}</span>
        </button>
      </nav>
    </aside>
    <main class="main-content">
      <section v-show="view==='papers'"><PapersView :jobs="jobs" :notify="notify" @source="source=$event" @changed="paperRevision++" /></section>
      <section v-show="view==='tasks'"><TasksView :jobs="jobs" :notify="notify" :paper-revision="paperRevision" @source="source=$event" @jobs="view='jobs'" /></section>
      <section v-show="view==='jobs'"><JobsView :jobs="jobs" :notify="notify" @source="source=$event" /></section>
      <section v-show="view==='planning'"><ResearchPlanningView @source="source=$event" @experiments="startExperiments" /></section>
      <section v-show="view==='experiments'"><ExperimentsView :workspace="experiments" @writing="view='writing'" /></section>
      <section v-show="view==='writing'"><PaperWritingView :workspace="experiments" @source="source=$event" @experiments="view='experiments'" /></section>
      <section v-show="view==='health'"><HealthView /></section>
    </main>
  </div>
  <EvidenceDrawer :source="source" @close="source=null" />
  <div class="toast-stack" aria-live="polite">
    <div v-for="toast in toasts" :key="toast.id" class="toast" :class="toast.type">
      <span>{{ toast.message }}</span>
      <button aria-label="关闭提示" @click="toasts=toasts.filter(x=>x.id!==toast.id)">×</button>
    </div>
  </div>
</template>
