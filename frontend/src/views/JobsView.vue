<script setup>
import { computed, ref } from 'vue'
import { downloadReport } from '../services/api'
import { isActive } from '../services/jobs'
import { dateText } from '../services/labels'
import JobSummary from '../components/JobSummary.vue'
import ReportPanel from '../components/ReportPanel.vue'
import JobHistory from '../components/JobHistory.vue'
const props=defineProps({jobs:Object,notify:Function})
defineEmits(['source'])
const historyKind=ref('report')
const filter=ref('all'),kind=ref('report'),jobId=ref(''),resuming=ref(false),error=ref(''),expanded=ref(''),downloading=ref('')
const items=computed(()=>props.jobs.entries.value.filter(x=>filter.value==='all'||x.kind===filter.value))
async function resume(){if(resuming.value)return;const id=Number(jobId.value);if(!Number.isSafeInteger(id)||id<=0){error.value='请输入有效的作业编号。';return}resuming.value=true;error.value='';try{await props.jobs.resume(kind.value,id);jobId.value=''}catch(cause){error.value=cause.message}finally{resuming.value=false}}
function openHistory(item){filter.value=item.kind;expanded.value=item.kind==='report'?item.key:''}
function adopted(item, result){props.jobs.track('report',item.ownerId,{...item.job,result:result.checked_report});props.notify('报告已更新。')}
async function download(item){downloading.value=item.key;try{await downloadReport(item.jobId);props.notify('报告已开始下载。')}catch(cause){props.notify(cause.message,'error')}finally{downloading.value=''}}
</script>
<template><header class="page-head"><div><h1>作业与报告</h1></div></header>
<section class="panel"><h3>查询已有作业</h3><form class="toolbar" @submit.prevent="resume"><select v-model="kind" aria-label="作业类型"><option value="report">报告作业</option><option value="preparation">论文准备作业</option></select><input v-model="jobId" required type="number" min="1" step="1" placeholder="作业编号" aria-label="作业编号" /><button class="primary" :disabled="resuming" type="submit">{{resuming?'查询中…':'查询并跟踪'}}</button></form><p class="hint">两类作业的编号各自独立，请先选对类型。也可在下方按论文或研究任务查历史，无需记住编号。</p><p v-if="error" class="notice error">{{error}}</p></section>
<nav class="tabs" aria-label="服务器历史类型"><button :class="{active:historyKind==='report'}" :aria-pressed="historyKind==='report'" @click="historyKind='report'">报告作业历史</button><button :class="{active:historyKind==='preparation'}" :aria-pressed="historyKind==='preparation'" @click="historyKind='preparation'">论文准备历史</button></nav>
<JobHistory :key="historyKind" :kind="historyKind" :jobs="jobs" @opened="openHistory" />
<h3>本浏览器跟踪的作业</h3>
<nav class="tabs"><button v-for="(label,key) in {all:'全部作业',preparation:'论文准备',report:'对比报告'}" :key="key" :class="{active:filter===key}" @click="filter=key">{{label}}</button></nav>
<article v-for="item in items" :key="item.key" class="panel job-card"><div class="row between"><div><span class="eyebrow">{{item.kind==='report'?'REPORT':'PREPARATION'}} / {{item.jobId}}</span><h2>{{item.kind==='report'?'对比报告':'论文准备'}} · {{item.kind==='report'?'任务':'论文'}} {{item.ownerId}}</h2></div><button class="text-button" :disabled="isActive(item.job)" @click="jobs.forget(item)">移出列表</button></div><JobSummary :item="item" /><p class="muted small">提交：{{dateText(item.job?.created_at)}}　开始：{{dateText(item.job?.started_at)}}　结束：{{dateText(item.job?.finished_at)}}</p><p v-if="item.job?.stage==='interrupted'" class="notice warning">旧作业已人工恢复为失败。确认服务正常后，请在论文库重新提交新作业。</p><div v-if="item.kind==='report'&&item.job?.result" class="actions"><button @click="expanded=expanded===item.key?'':item.key">{{expanded===item.key?'收起报告':'查看报告'}}</button><button class="primary" :disabled="downloading===item.key" @click="download(item)">{{downloading===item.key?'下载中…':'下载 Markdown'}}</button></div><template v-if="expanded===item.key"><p class="hint">报告基于生成时的分析快照，后续修改任务不会自动更新这份报告。</p><ReportPanel :job-id="item.jobId" :result="item.job?.result" @adopted="adopted(item,$event)" @source="$emit('source',$event)" /></template></article><p v-if="!items.length" class="empty">暂无跟踪作业。可以提交新作业，或输入已有编号查询。</p></template>
