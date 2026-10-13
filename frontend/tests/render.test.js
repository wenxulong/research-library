import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
let server
before(async()=>{server=await createServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'})})
after(async()=>{await server?.close()})
async function render(path,props){const module=await server.ssrLoadModule(path);return renderToString(createSSRApp(module.default,props))}
const source={source_number:23,paper_id:9,paper_title:'语义缓存',chunk_id:901,page_number:4,score:null}
const report={report:{method_comparisons:[{text:'对比结论[资料23]',source_numbers:[23]}],experiment_comparisons:[],conclusion_comparisons:[],evidence_notes:[],sources:[source]},review:{items:[{claim:'对比结论',verdict:'insufficient_evidence',reason:'证据不足'}]},correction_rounds:1}
test('report completion is not confused with passed review',async()=>{const html=await render('/src/components/ReportPanel.vue',{result:{...report,passed:false}});assert.match(html,/待核验报告/);assert.doesNotMatch(html,/本轮审校通过/);assert.match(html,/作业已完成，但报告仍有未通过审校/)})
test('verified report has distinct review badge',async()=>{const html=await render('/src/components/ReportPanel.vue',{result:{...report,passed:true}});assert.match(html,/本轮审校通过/)})
test('sparse evidence number is preserved and never renumbered',async()=>{const html=await render('/src/components/SourcesPanel.vue',{sources:[source]});assert.match(html,/资料 23/);assert.doesNotMatch(html,/资料 1</);assert.match(html,/论文 9/);assert.match(html,/文本块 901/)})
test('model text is escaped, not interpreted as HTML',async()=>{const html=await render('/src/components/AnalysisPanel.vue',{analysis:{summary:'<script>alert(1)</script>',methods:[],experiments:[],conclusions:[],limitations:[],evidence_notes:[],sources:[]}});assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>alert/)})
test('reused preparation reports zero newly created vectors',async()=>{const html=await render('/src/components/JobSummary.vue',{item:{kind:'preparation',jobId:6,job:{status:'succeeded',stage:'finished',result:{page_count:10,chunk_count:146,indexed_count:0}}}});assert.match(html,/<strong>0<\/strong>条新增向量/);assert.match(html,/已完成/)})
