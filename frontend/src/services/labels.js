export const fields = { summary:'研究概述', methods:'方法', experiments:'实验', conclusions:'结论', limitations:'局限', evidence_notes:'证据备注' }
export const verdicts = { supported:'证据支持', partially_supported:'部分支持', insufficient_evidence:'证据不足', contradicted:'与原文矛盾', appropriate:'分类合适', misplaced:'分类不合适', mixed:'混合陈述', uncertain:'需要确认' }
export const stages = { waiting:'等待处理', extracting:'提取正文', chunking:'文本分块', indexing:'生成向量', generating:'生成报告', processing:'补证与审校', assembling:'整理报告', finished:'处理完成', failed:'处理失败', interrupted:'执行中断' }
export function dateText(value) { return value ? String(value).replace('T', ' ').slice(0,19) : '—' }

// 这是整条陈述的复审核查依据，不推断每个片段各自的支持/反对立场。
export const reviewEvidenceLabels = {
  supported: '支持判断的复审依据',
  contradicted: '反对证据／矛盾核查依据（不作为支持引用）',
  partially_supported: '适用条件与限制的核查依据',
  insufficient_evidence: '证据缺口的核查资料（尚不能支持结论）',
}
