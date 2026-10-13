<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { papersApi } from '../services/api'
import { isActive } from '../services/jobs'
import BaseDialog from '../components/BaseDialog.vue'
import JobSummary from '../components/JobSummary.vue'
const props=defineProps({ jobs:Object, notify:Function })
const emit=defineEmits(['source','changed'])
const papers=ref([]), loading=ref(false), error=ref(''), search=ref(''), saving=ref(false), editor=ref(false), editingId=ref(null), removing=ref(null), formError=ref('')
const documents=reactive({}), docErrors=reactive({}), busy=reactive({})
const empty=()=>({title:'',author:'',year:new Date().getFullYear(),notes:''})
const form=reactive(empty())
const filtered=computed(()=> papers.value.filter(p => `${p.title} ${p.author} ${p.notes}`.toLowerCase().includes(search.value.toLowerCase())))
let sequence=0
async function refresh() {
  const token=++sequence; loading.value=true; error.value=''
  try { const list=await papersApi.list(); if(token!==sequence)return; papers.value=list.sort((a,b)=>b.id-a.id)
    await Promise.all(list.map(async p=>{try { const doc=await papersApi.document(p.id); if(token===sequence){documents[p.id]=doc;docErrors[p.id]=''} }catch(cause){if(token===sequence)docErrors[p.id]=cause.message}}))
  }catch(cause){if(token===sequence)error.value=cause.message}
  finally{if(token===sequence)loading.value=false}
}
function edit(paper=null){ editingId.value=paper?.id || null; Object.assign(form,paper ? {title:paper.title,author:paper.author,year:paper.year,notes:paper.notes} : empty()); formError.value=''; editor.value=true }
async function save(){if(saving.value)return; const data={title:form.title.trim(),author:form.author.trim(),year:Number(form.year),notes:form.notes.trim()}; if(!data.title||!data.author){formError.value='标题和作者不能为空。';return} saving.value=true;formError.value=''
  try{if(editingId.value)await papersApi.update(editingId.value,data);else await papersApi.create(data); editor.value=false;props.notify('论文已保存。');emit('changed');await refresh()}catch(cause){formError.value=cause.message}finally{saving.value=false}
}
async function upload(paper,event){const file=event.target.files?.[0];event.target.value='';if(!file)return;if(!file.name.toLowerCase().endsWith('.pdf')){props.notify('请选择PDF文件。','error');return}busy[paper.id]=true
  try{documents[paper.id]=await papersApi.upload(paper.id,file);docErrors[paper.id]='';props.notify('PDF已上传，可以开始后台准备。')}catch(cause){props.notify(cause.message,'error')}finally{busy[paper.id]=false}
}
async function prepare(paper){busy[paper.id]=true;try{const job=await papersApi.prepare(paper.id);props.jobs.track('preparation',paper.id,job);props.notify(`已提交准备作业 ${job.id}。`)}catch(cause){if(cause.jobId)props.jobs.track('preparation',paper.id,{id:cause.jobId,status:'queued'});props.notify(cause.message,'error')}finally{busy[paper.id]=false}}
function paperJob(paper){const item=props.jobs.latest('preparation',paper.id);const doc=documents[paper.id];if(!doc || (item?.job?.document_id && item.job.document_id!==doc.id))return null;return item}
function blocked(paper){return busy[paper.id]||isActive(props.jobs.latest('preparation',paper.id)?.job)}
async function confirmRemove(){const target=removing.value;if(!target||saving.value)return;saving.value=true
  try{if(target.document){await papersApi.removeDocument(target.paper.id);documents[target.paper.id]=null;props.notify('PDF和处理数据已删除。')}else{await papersApi.remove(target.paper.id);props.notify('论文已删除。');emit('changed');await refresh()}removing.value=null}catch(cause){props.notify(cause.message,'error')}finally{saving.value=false}}
onMounted(refresh)
defineExpose({ refresh })
</script>
<template>
  <header class="page-head"><div><p class="eyebrow">资料准备 / LIBRARY</p><h1>把论文变成研究资料</h1><p class="muted">上传 PDF，后台提取正文、分块并建立检索向量。</p></div><button class="primary" @click="edit()">＋ 添加论文</button></header>
  <div class="toolbar"><label class="search"><span>搜索</span><input v-model="search" placeholder="标题、作者或备注" aria-label="搜索论文" /></label><span class="muted">{{ papers.length }} 篇论文</span><button :disabled="loading" @click="refresh">{{ loading?'读取中…':'刷新' }}</button></div>
  <p v-if="error" class="notice error" role="alert">{{ error }}</p><p v-if="loading && !papers.length" class="empty">正在读取论文…</p>
  <div v-else-if="filtered.length" class="paper-grid"><article v-for="paper in filtered" :key="paper.id" class="paper-card"><div class="row between"><span class="eyebrow">PAPER / {{ paper.id }}</span><button class="text-button" @click="edit(paper)">编辑</button></div><h2>{{ paper.title }}</h2><p class="muted">{{ paper.author }} · {{ paper.year }}</p><p v-if="paper.notes" class="notes">{{ paper.notes }}</p>
    <p v-if="docErrors[paper.id]" class="notice error">{{ docErrors[paper.id] }}</p>
    <div v-else-if="documents[paper.id]" class="file-box"><strong>PDF 已上传</strong><span>{{ documents[paper.id].original_name }}</span><small>{{ (documents[paper.id].file_size/1024/1024).toFixed(2) }} MB</small></div>
    <div v-else class="file-box empty-file"><span>{{ documents[paper.id]===null ? '尚未上传 PDF' : '正在查询 PDF…' }}</span><label class="upload-label">选择 PDF<input type="file" accept=".pdf,application/pdf" :disabled="blocked(paper) || documents[paper.id]!==null" @change="upload(paper,$event)" /></label></div>
    <JobSummary :item="paperJob(paper)" />
    <div class="card-actions"><button class="primary" :disabled="blocked(paper)||!documents[paper.id]" @click="prepare(paper)">{{ blocked(paper)?'正在处理…':'后台准备' }}</button><button :disabled="!documents[paper.id]" @click="emit('source',{paper_id:paper.id,paper_title:paper.title,page_number:1})">查看正文</button></div>
    <div class="card-footer"><button v-if="documents[paper.id]" class="text-button danger" :disabled="blocked(paper)" @click="removing={paper,document:true}">删除 PDF</button><button class="text-button danger" :disabled="blocked(paper)" @click="removing={paper,document:false}">删除论文</button></div>
  </article></div><p v-else-if="!loading" class="empty">{{ search?'没有匹配的论文。':'从添加第一篇论文开始。' }}</p>
  <BaseDialog :open="editor" :title="editingId?'编辑论文':'添加论文'" @close="!saving && (editor=false)"><form @submit.prevent="save"><fieldset :disabled="saving"><label>标题<input v-model="form.title" required maxlength="200" /></label><div class="form-grid"><label>作者<input v-model="form.author" required maxlength="100" /></label><label>年份<input v-model.number="form.year" required type="number" min="1000" max="9999" step="1" /></label></div><label>备注<textarea v-model="form.notes" maxlength="1000" rows="3"></textarea></label><p v-if="formError" class="notice error">{{ formError }}</p><div class="actions"><button type="button" @click="editor=false">取消</button><button class="primary" type="submit">{{ saving?'保存中…':'保存论文' }}</button></div></fieldset></form></BaseDialog>
  <BaseDialog :open="!!removing" :title="removing?.document?'删除 PDF':'删除论文'" @close="!saving && (removing=null)"><p>确认删除「{{ removing?.paper.title }}」{{ removing?.document?'的PDF和已提取的正文、文本块、向量':'及其PDF、处理数据和任务关联' }}？此操作不能撤销。</p><template #actions><button :disabled="saving" @click="removing=null">取消</button><button class="danger-button" :disabled="saving" @click="confirmRemove">{{ saving?'删除中…':'确认删除' }}</button></template></BaseDialog>
</template>
