import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createExperimentWorkspace } from '../src/composables/useExperimentWorkspace.js'
let server
before(async()=>{server=await createServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'})})
after(async()=>{await server?.close()})
async function render(path,props){const module=await server.ssrLoadModule(path);return renderToString(createSSRApp(module.default,props))}
const source={source_number:23,paper_id:9,paper_title:'语义缓存',chunk_id:901,page_number:4,score:null}
const report={report:{method_comparisons:[{text:'对比结论[资料23]',source_numbers:[23]}],experiment_comparisons:[],conclusion_comparisons:[],evidence_notes:[],sources:[source]},review:{items:[{claim:'对比结论',verdict:'insufficient_evidence',reason:'证据不足'}]},correction_rounds:1}
test('numbered report exposes scoped investigation while legacy report explains missing ids',async()=>{
 const legacy=await render('/src/components/ReportPanel.vue',{result:report,jobId:19})
 assert.match(legacy,/旧报告无陈述编号/)
 const numbered={...report,report:{...report.report,method_comparisons:[{...report.report.method_comparisons[0],claim_id:'claim-a'}]}}
 const html=await render('/src/components/ReportPanel.vue',{result:numbered,jobId:19})
 assert.match(html,/继续查证/);assert.match(html,/开始查证/);assert.match(html,/读取已保存记录/)
 assert.match(html,/不会覆盖原报告/);assert.doesNotMatch(html,/旧报告无陈述编号/)
})
test('report completion is not confused with passed review',async()=>{const html=await render('/src/components/ReportPanel.vue',{result:{...report,passed:false}});assert.match(html,/待核验报告/);assert.doesNotMatch(html,/本轮审校通过/);assert.match(html,/作业已完成，但报告仍有未通过审校/)})
test('verified report has distinct review badge',async()=>{const html=await render('/src/components/ReportPanel.vue',{result:{...report,passed:true}});assert.match(html,/本轮审校通过/)})
test('sparse evidence number is preserved and never renumbered',async()=>{const html=await render('/src/components/SourcesPanel.vue',{sources:[source]});assert.match(html,/资料 23/);assert.doesNotMatch(html,/资料 1</);assert.match(html,/论文 9/);assert.match(html,/文本块 901/)})
test('model text is escaped, not interpreted as HTML',async()=>{const html=await render('/src/components/AnalysisPanel.vue',{analysis:{summary:'<script>alert(1)</script>',methods:[],experiments:[],conclusions:[],limitations:[],evidence_notes:[],sources:[]}});assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>alert/)})
test('reused preparation reports zero newly created vectors',async()=>{const html=await render('/src/components/JobSummary.vue',{item:{kind:'preparation',jobId:6,job:{status:'succeeded',stage:'finished',result:{page_count:10,chunk_count:146,indexed_count:0}}}});assert.match(html,/<strong>0<\/strong>条新增向量/);assert.match(html,/已完成/)})
test('jobs view exposes server history separately from browser tracking',async()=>{const html=await render('/src/views/JobsView.vue',{jobs:{entries:{value:[]},resume:async()=>{}},notify:()=>{}});assert.match(html,/研究任务的报告历史/);assert.match(html,/选择历史报告所属任务/);assert.match(html,/本浏览器跟踪的作业/);assert.match(html,/查询已有作业/)})
test('preparation history has paper selector and does not label it as task history',async()=>{const html=await render('/src/components/JobHistory.vue',{kind:'preparation',jobs:{resume:async()=>{}}});assert.match(html,/论文准备作业历史/);assert.match(html,/选择历史作业所属论文/);assert.match(html,/刷新论文/);assert.doesNotMatch(html,/选择历史报告所属任务/)})
test('research planning view exposes report selection and saved question recovery',async()=>{const html=await render('/src/views/ResearchPlanningView.vue',{});assert.match(html,/选择研究任务/);assert.match(html,/生成候选问题/);assert.match(html,/打开问题集/);assert.match(html,/继续已有问题集/)})
test('research plan panel renders all experiment sections and escapes model text',async()=>{const html=await render('/src/components/ResearchPlanPanel.vue',{plan:{question_set_id:7,task_id:21,research_question:'测试问题',content:{objective:'<script>unsafe</script>',hypothesis:'待验证',data_plan:'准备数据',method_steps:['方法'],baselines:['基线'],metrics:['延迟'],main_experiments:['主实验'],ablation_experiments:[],execution_steps:['步骤'],risks:['风险'],result_columns:['method','latency_ms']}}});assert.match(html,/消融实验/);assert.match(html,/待确认事项与风险/);assert.match(html,/method,latency_ms/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>unsafe/);assert.match(html,/本方案未安排这一项/)})
test('experiment and writing views expose record recovery without calling models on mount',async()=>{const workspace=createExperimentWorkspace({storage:null});let html=await render('/src/views/ExperimentsView.vue',{workspace});assert.match(html,/载入研究方案/);assert.match(html,/打开实验记录/);html=await render('/src/views/PaperWritingView.vue',{workspace});assert.match(html,/实验导入编号/);assert.match(html,/请先打开已有实验记录/)})
test('statistics render missing values distinctly from zero and do not graph invalid-only groups',async()=>{const html=await render('/src/components/ExperimentStatisticsPanel.vue',{importId:9,dataset:'demo',scenario:'normal',statistics:[{metric:'score',status_available:false,groups:[{method:'baseline',mean:null,stddev:null,valid_count:0,total_count:1,failed_count:0,missing_count:1,invalid_count:0,unknown_status_count:0}]}]});assert.match(html,/没有status列/);assert.match(html,/没有有效数值/);assert.match(html,/—/);assert.doesNotMatch(html,/<img/)})
test('analysis renders all interpretation sections and escapes model content',async()=>{const html=await render('/src/components/ExperimentAnalysisPanel.vue',{analysis:{content:{summary:'<script>unsafe</script>',differences:['差异'],tradeoffs:['权衡'],limitations:['局限'],next_experiments:['后续']}}});assert.match(html,/多指标权衡/);assert.match(html,/后续实验建议/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>unsafe/)})
test('paper preview preserves source identifiers while deduplicating bibliography per paper',async()=>{const evidence=[{...source,source_number:23},{...source,source_number:41,chunk_id:902},{...source,source_number:50,paper_id:10,paper_title:'第二篇'}];const sections=Object.fromEntries(['introduction','related_work','methods','experiments','discussion','conclusion'].map(field=>[field,[{text:field==='introduction'?'<script>unsafe</script>':'正文',source_numbers:field==='introduction'?[23,41,50]:[]}]]));const html=await render('/src/components/PaperDraftPanel.vue',{draft:{import_id:9,content:{title:'标题',abstract:'摘要',...sections},evidence,missing_information:['待补充方法']}});assert.match(html,/查看参考文献1的原文/);assert.match(html,/查看参考文献2的原文/);assert.doesNotMatch(html,/查看参考文献3的原文/);assert.match(html,/资料 41/);assert.match(html,/待补充方法/);assert.match(html,/&lt;script&gt;/);assert.match(html,/6 结论/)})


test('writing expansion exposes confirmation, editable suggestions and escaped evidence numbers', async () => {
  const workspace = createExperimentWorkspace({ storage: null })
  workspace.imported.value = { id: 9, rows: [], plan_snapshot: { research_question: '问题' } }
  const content = { title: '论文', abstract: '摘要', ...Object.fromEntries(['introduction', 'related_work', 'methods', 'experiments', 'discussion', 'conclusion'].map(field => [field, [{ text: '原正文', source_numbers: [] }]])) }
  workspace.draft.value = { id: 42, import_id: 9, content, evidence: [], missing_information: [] }
  workspace.expansionSuggestion.value = { draft_id: 42, section: 'methods', paragraphs: [{ text: '<script>unsafe</script>', source_numbers: [23, 41] }] }
  const html = await render('/src/views/PaperWritingView.vue', { workspace })
  assert.match(html, /选择章节/)
  assert.match(html, /扩写要求/)
  assert.match(html, /确认替换本章并保存/)
  assert.match(html, /放弃建议/)
  assert.match(html, /尚未保存/)
  assert.match(html, /23、41/)
  assert.match(html, /&lt;script&gt;/)
  assert.doesNotMatch(html, /<script>unsafe/)
})

test('Skill entry renders clarification safely and retains manual expansion', async () => {
  const workspace = createExperimentWorkspace({ storage: null })
  workspace.imported.value = { id: 9, rows: [], plan_snapshot: { research_question: '问题' } }
  const content = { title: '论文', abstract: '摘要', ...Object.fromEntries(['introduction', 'related_work', 'methods', 'experiments', 'discussion', 'conclusion'].map(field => [field, [{ text: '正文', source_numbers: [] }]])) }
  workspace.draft.value = { id: 42, import_id: 9, content, evidence: [], missing_information: [] }
  workspace.skillQuestion.value = '<script>unsafe</script>请选择章节'
  const html = await render('/src/views/PaperWritingView.vue', { workspace })
  assert.match(html, /一句话扩写（Skill）/)
  assert.match(html, /执行写作 Skill/)
  assert.match(html, /或手动选择章节/)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /补全要求后重新执行/)
  assert.doesNotMatch(html, /<script>unsafe/)
})

test('contradicted review evidence appears beside statement separately from its citations', async () => {
  const statement = { text: '原陈述[资料23]', claim_id: 'claim-x', source_numbers: [23] }
  const result = {
    ...report, passed: false,
    report: { ...report.report, method_comparisons: [statement] },
    review: { items: [{ claim_id: 'claim-x', claim: statement.text,
      verdict: 'contradicted', reason: '低重复率场景存在反例', source_numbers: [23, 41] }] },
  }
  const html = await render('/src/components/ReportPanel.vue', { result })
  assert.match(html, /反对证据／矛盾核查依据（不作为支持引用）：23、41/)
  assert.match(html, /低重复率场景存在反例/)
  assert.match(html, /正文引用（是否成立以审校为准）：23/)
  assert.doesNotMatch(html, /原陈述\[资料23\]\[资料41\]/)
  assert.match(html, /待核验报告/)
})

test('partial and insufficient reviews show conditions or gaps rather than support labels', async () => {
  for (const [verdict, label] of [
    ['partially_supported', '适用条件与限制的核查依据'],
    ['insufficient_evidence', '证据缺口的核查资料（尚不能支持结论）'],
  ]) {
    const result = {
      ...report, passed: false,
      report: { ...report.report, method_comparisons: [{ text: '陈述[资料23]', claim_id: 'x', source_numbers: [23] }] },
      review: { items: [{ claim_id: 'x', claim: '陈述', verdict, reason: '需要核验条件', source_numbers: [41] }] },
    }
    const html = await render('/src/components/ReportPanel.vue', { result })
    assert.ok(html.includes(`${label}：41`))
    assert.doesNotMatch(html, /支持判断的复审依据/)
  }
})
