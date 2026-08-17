<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as api from '@/api/ontology'
import type { DraftBundle, GovernanceFinding, OntologyDraftManifest } from '@/api/ontology'

const drafts = ref<OntologyDraftManifest[]>([])
const activeId = ref('')
const bundle = ref<DraftBundle | null>(null)
const loading = ref(false)
const saving = ref(false)
const activeEditor = ref('ontology')
const createOpen = ref(false)
const entityOpen = ref(false)
const impactOpen = ref(false)
const impact = ref<Awaited<ReturnType<typeof api.previewImpact>> | null>(null)

const editor = reactive({ ontology_text: '', shapes_text: '', skos_text: '' })
const actor = ref('editor:local-user')
const createForm = reactive({ ontology_id: '', created_by: 'editor:local-user', ontology_text: '', shapes_text: '', skos_text: '' })
const entityForm = reactive({ kind: 'class', prefix: 'ex', iri: 'https://example.com/ontology/', name: '', labelZh: '', domain: '', range: '', broader: '' })

const latestReview = computed(() => bundle.value?.reviews.at(-1))
const waivedFindings = computed(() => new Set(bundle.value?.waivers.map((item) => item.finding_id) ?? []))
const stateStep = computed(() => {
  const state = bundle.value?.manifest.state
  if (state === 'published') return 4
  if (state === 'approved') return 3
  if (state === 'validated' || state === 'conflict_review') return 2
  return 1
})

const stateLabels: Record<string, string> = {
  draft: '草稿', changes_requested: '待修改', validated: '校验通过', conflict_review: '冲突审查', approved: '已审批', published: '已发布',
}

const defaultOntology = `@prefix ex: <https://example.com/ontology/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:BusinessEntity a owl:Class .
ex:sample a ex:BusinessEntity ; ex:name "Sample"^^xsd:string .`

const defaultShapes = `@prefix ex: <https://example.com/ontology/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:BusinessEntityShape a sh:NodeShape ;
  sh:targetClass ex:BusinessEntity ;
  sh:property [ sh:path ex:name ; sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:string ] .`

const defaultSkos = `@prefix ex: <https://example.com/ontology/> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .

ex:businessEntity a skos:Concept ;
  skos:prefLabel "业务实体"@zh ;
  skos:prefLabel "Business entity"@en .`

async function refreshDrafts() {
  drafts.value = (await api.listDrafts()).drafts
}

async function openDraft(id: string) {
  activeId.value = id
  loading.value = true
  try {
    bundle.value = await api.getDraft(id)
    editor.ontology_text = bundle.value.ontology_text
    editor.shapes_text = bundle.value.shapes_text
    editor.skos_text = bundle.value.skos_text
  } finally { loading.value = false }
}

function showCreate() {
  Object.assign(createForm, { ontology_id: '', created_by: actor.value, ontology_text: defaultOntology, shapes_text: defaultShapes, skos_text: defaultSkos })
  createOpen.value = true
}

async function createDraft() {
  const created = await api.createDraft(createForm)
  createOpen.value = false
  await refreshDrafts(); await openDraft(created.draft_id)
  ElMessage.success('草稿已创建，尚未进入发布门禁')
}

async function saveDraft() {
  if (!bundle.value) return
  saving.value = true
  try {
    await api.updateDraft(activeId.value, { actor: actor.value, ...editor })
    await openDraft(activeId.value); await refreshDrafts()
    ElMessage.success('新修订已保存，需重新校验')
  } finally { saving.value = false }
}

async function validate() {
  if (!bundle.value) return
  const result = await api.validateDraft(activeId.value, actor.value)
  await openDraft(activeId.value); await refreshDrafts()
  result.conforms ? ElMessage.success('SHACL / OWL / SKOS 门禁通过') : ElMessage.warning(`发现 ${result.findings.length} 项治理问题`)
}

async function waive(finding: GovernanceFinding) {
  const rationale = await ElMessageBox.prompt('说明为何允许该违规暂时存在；原始违规不会被删除。', '记录不可变豁免', { inputPlaceholder: '整改工单、业务理由与风险控制', confirmButtonText: '下一步' }).then((r) => r.value)
  const policy = await ElMessageBox.prompt('填写授权该豁免的策略或制度编号。', '绑定治理策略', { inputPlaceholder: 'policy:legacy-waiver-v1', confirmButtonText: '确认豁免' }).then((r) => r.value)
  await api.waiveFinding(activeId.value, { finding_id: finding.finding_id, actor: actor.value, rationale, policy })
  await openDraft(activeId.value)
  ElMessage.success('豁免已追加记录，原违规仍保留在审查轨迹中')
}

async function approve() {
  const rationale = await ElMessageBox.prompt('审批理由会写入决策溯源账本。', '审批本体修订', { inputPlaceholder: '说明校验结果、豁免判断与发布依据', confirmButtonText: '批准' }).then((r) => r.value)
  await api.approveDraft(activeId.value, { approver: actor.value, rationale, policies: ['policy:ontology-release-gate'] })
  await openDraft(activeId.value); await refreshDrafts()
  ElMessage.success('审批决策已写入溯源账本')
}

async function requestChanges() {
  const rationale = await ElMessageBox.prompt('指出需要修改的约束、术语或关系。该审查意见会写入决策账本。', '请求修改', { inputPlaceholder: '说明冲突与期望修订', confirmButtonText: '转入修改' }).then((r) => r.value)
  await api.requestChanges(activeId.value, actor.value, rationale)
  await openDraft(activeId.value); await refreshDrafts()
  ElMessage.success('已转入待修改状态；保存时会生成新修订并要求重新校验')
}

async function publish() {
  await ElMessageBox.confirm('发布后该版本不可覆盖；后续修改必须创建新草稿与新版本。', '发布不可变版本', { type: 'warning', confirmButtonText: '发布', cancelButtonText: '取消' })
  const result = await api.publishDraft(activeId.value, actor.value)
  await openDraft(activeId.value); await refreshDrafts()
  ElMessage.success(`已发布 ${result.release.ontology_version}`)
}

async function showImpact() {
  impact.value = await api.previewImpact(activeId.value)
  impactOpen.value = true
}

function appendEntity() {
  const p = entityForm.prefix || 'ex'
  const prefixLine = `@prefix ${p}: <${entityForm.iri}> .`
  const ensurePrefix = (target: 'ontology_text' | 'skos_text', line: string) => {
    if (!editor[target].includes(line)) editor[target] = `${line}\n${editor[target]}`
  }
  if (entityForm.kind === 'class') {
    ensurePrefix('ontology_text', '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .')
    ensurePrefix('ontology_text', '@prefix owl: <http://www.w3.org/2002/07/owl#> .')
    if (!editor.ontology_text.includes(prefixLine)) editor.ontology_text += `\n${prefixLine}\n`
    editor.ontology_text += `\n${p}:${entityForm.name} a owl:Class ;\n  rdfs:label "${entityForm.labelZh || entityForm.name}"@zh .\n`
    activeEditor.value = 'ontology'
  } else if (entityForm.kind === 'property') {
    ensurePrefix('ontology_text', '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .')
    ensurePrefix('ontology_text', '@prefix owl: <http://www.w3.org/2002/07/owl#> .')
    if (!editor.ontology_text.includes(prefixLine)) editor.ontology_text += `\n${prefixLine}\n`
    editor.ontology_text += `\n${p}:${entityForm.name} a owl:ObjectProperty ;\n  rdfs:domain ${p}:${entityForm.domain} ;\n  rdfs:range ${p}:${entityForm.range} .\n`
    activeEditor.value = 'ontology'
  } else {
    ensurePrefix('skos_text', '@prefix skos: <http://www.w3.org/2004/02/skos/core#> .')
    if (!editor.skos_text.includes(prefixLine)) editor.skos_text += `\n${prefixLine}\n`
    editor.skos_text += `\n${p}:${entityForm.name} a skos:Concept ;\n  skos:prefLabel "${entityForm.labelZh || entityForm.name}"@zh${entityForm.broader ? ` ;\n  skos:broader ${p}:${entityForm.broader}` : ''} .\n`
    activeEditor.value = 'skos'
  }
  entityOpen.value = false
  ElMessage.info('已加入当前编辑缓冲区，保存后生成新修订')
}

onMounted(async () => {
  await refreshDrafts()
  if (drafts.value.length) await openDraft(drafts.value[0].draft_id)
})
</script>

<template>
  <div class="governance-page">
    <header class="hero">
      <div>
        <div class="eyebrow">SEMANTIC CONTROL PLANE / 语义控制面</div>
        <h1>本体治理工作台</h1>
        <p>OWL 管可计算语义，SKOS 管业务语言，SHACL 决定它们是否有资格发布。</p>
      </div>
      <div class="hero-actions">
        <el-input v-model="actor" class="actor" placeholder="当前操作者" />
        <el-button class="new-button" @click="showCreate">＋ 新建治理草稿</el-button>
      </div>
    </header>

    <section class="process-rail">
      <div v-for="(label, index) in ['草稿编辑', '约束校验', '冲突审查', '批准发布']" :key="label" class="process-node" :class="{ active: stateStep >= index + 1 }">
        <span>{{ String(index + 1).padStart(2, '0') }}</span><strong>{{ label }}</strong>
      </div>
    </section>

    <div class="workbench">
      <aside class="draft-rail">
        <div class="rail-title"><span>修订队列</span><b>{{ drafts.length }}</b></div>
        <button v-for="draft in drafts" :key="draft.draft_id" class="draft-card" :class="{ selected: activeId === draft.draft_id }" @click="openDraft(draft.draft_id)">
          <span class="draft-name">{{ draft.ontology_id }}</span>
          <span class="draft-meta">REV {{ draft.revision }} · {{ stateLabels[draft.state] }}</span>
          <i :class="`state-${draft.state}`"></i>
        </button>
        <div v-if="!drafts.length" class="empty-rail">尚无草稿<br>从受控模板开始</div>
      </aside>

      <main v-loading="loading" class="editor-shell">
        <template v-if="bundle">
          <div class="editor-topbar">
            <div>
              <span class="mono-id">{{ bundle.manifest.draft_id }}</span>
              <h2>{{ bundle.manifest.ontology_id }} <small>revision {{ bundle.manifest.revision }}</small></h2>
            </div>
            <div class="status-block">
              <span>当前状态</span><strong>{{ stateLabels[bundle.manifest.state] }}</strong>
            </div>
          </div>

          <el-tabs v-model="activeEditor" class="semantic-tabs">
            <el-tab-pane label="OWL · 计算语义" name="ontology"><textarea v-model="editor.ontology_text" spellcheck="false" /></el-tab-pane>
            <el-tab-pane label="SHACL · 发布契约" name="shapes"><textarea v-model="editor.shapes_text" spellcheck="false" /></el-tab-pane>
            <el-tab-pane label="SKOS · 业务词汇" name="skos"><textarea v-model="editor.skos_text" spellcheck="false" /></el-tab-pane>
          </el-tabs>

          <div class="action-strip">
            <el-button :disabled="!['draft', 'changes_requested'].includes(bundle.manifest.state)" @click="entityOpen = true">结构化添加</el-button>
            <el-button :loading="saving" :disabled="!['draft', 'changes_requested'].includes(bundle.manifest.state)" @click="saveDraft">保存新修订</el-button>
            <el-button type="primary" :disabled="!['draft', 'changes_requested'].includes(bundle.manifest.state)" @click="validate">运行治理门禁</el-button>
            <el-button @click="showImpact">影响预览</el-button>
            <span class="action-spacer"></span>
            <el-button :disabled="!['validated', 'conflict_review'].includes(bundle.manifest.state)" @click="requestChanges">请求修改</el-button>
            <el-button type="warning" :disabled="!['validated', 'conflict_review'].includes(bundle.manifest.state)" @click="approve">审批</el-button>
            <el-button class="publish-button" :disabled="bundle.manifest.state !== 'approved'" @click="publish">发布不可变版本</el-button>
          </div>

          <section class="review-panel">
            <div class="review-heading">
              <div><span class="eyebrow">LATEST GATE EVIDENCE</span><h3>最新门禁证据</h3></div>
              <el-tag v-if="latestReview" :type="latestReview.conforms ? 'success' : 'danger'">{{ latestReview.conforms ? 'CONFORMS' : `${latestReview.findings.length} FINDINGS` }}</el-tag>
            </div>
            <el-table v-if="latestReview?.findings.length" :data="latestReview.findings" size="small" class="finding-table">
              <el-table-column prop="severity" label="级别" width="88" />
              <el-table-column prop="constraint_component" label="约束" width="220" />
              <el-table-column prop="focus_node" label="焦点实体" min-width="220" show-overflow-tooltip />
              <el-table-column prop="message" label="说明" min-width="260" />
              <el-table-column label="处置" width="110">
                <template #default="{ row }">
                  <el-tag v-if="waivedFindings.has(row.finding_id)" type="warning" size="small">已豁免</el-tag>
                  <el-button v-else link type="warning" @click="waive(row)">记录豁免</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-else class="clean-gate"><span>✓</span><div><strong>{{ latestReview ? '约束集全部满足' : '等待首次校验' }}</strong><p>{{ latestReview ? '当前修订没有未解决的 SHACL、OWL 或 SKOS 冲突。' : '保存草稿后运行治理门禁，生成不可变审查证据。' }}</p></div></div>
          </section>
        </template>
        <div v-else class="select-empty">选择一个草稿，或创建新的语义修订。</div>
      </main>
    </div>

    <el-dialog v-model="createOpen" title="创建受治理本体草稿" width="78%" top="4vh">
      <el-form label-position="top">
        <el-row :gutter="16"><el-col :span="12"><el-form-item label="本体 ID"><el-input v-model="createForm.ontology_id" placeholder="customer-domain" /></el-form-item></el-col><el-col :span="12"><el-form-item label="创建者"><el-input v-model="createForm.created_by" /></el-form-item></el-col></el-row>
        <el-tabs><el-tab-pane label="OWL"><el-input v-model="createForm.ontology_text" type="textarea" :rows="12" /></el-tab-pane><el-tab-pane label="SHACL"><el-input v-model="createForm.shapes_text" type="textarea" :rows="12" /></el-tab-pane><el-tab-pane label="SKOS"><el-input v-model="createForm.skos_text" type="textarea" :rows="12" /></el-tab-pane></el-tabs>
      </el-form>
      <template #footer><el-button @click="createOpen = false">取消</el-button><el-button type="primary" :disabled="!createForm.ontology_id" @click="createDraft">创建草稿</el-button></template>
    </el-dialog>

    <el-dialog v-model="entityOpen" title="结构化添加语义实体" width="560px">
      <el-form label-position="top">
        <el-form-item label="实体类型"><el-segmented v-model="entityForm.kind" :options="[{ label: 'OWL 类', value: 'class' }, { label: 'OWL 关系属性', value: 'property' }, { label: 'SKOS 术语', value: 'concept' }]" /></el-form-item>
        <el-row :gutter="12"><el-col :span="7"><el-form-item label="前缀"><el-input v-model="entityForm.prefix" /></el-form-item></el-col><el-col :span="17"><el-form-item label="命名空间 IRI"><el-input v-model="entityForm.iri" /></el-form-item></el-col></el-row>
        <el-form-item label="本地名称"><el-input v-model="entityForm.name" placeholder="CustomerAccount" /></el-form-item>
        <el-form-item label="中文首选标签"><el-input v-model="entityForm.labelZh" placeholder="客户账户" /></el-form-item>
        <el-row v-if="entityForm.kind === 'property'" :gutter="12"><el-col :span="12"><el-form-item label="Domain 类"><el-input v-model="entityForm.domain" /></el-form-item></el-col><el-col :span="12"><el-form-item label="Range 类"><el-input v-model="entityForm.range" /></el-form-item></el-col></el-row>
        <el-form-item v-if="entityForm.kind === 'concept'" label="上位术语（可选）"><el-input v-model="entityForm.broader" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="entityOpen = false">取消</el-button><el-button type="primary" :disabled="!entityForm.name || (entityForm.kind === 'property' && (!entityForm.domain || !entityForm.range))" @click="appendEntity">加入编辑缓冲区</el-button></template>
    </el-dialog>

    <el-drawer v-model="impactOpen" title="发布影响预览" size="52%">
      <template v-if="impact"><div class="impact-summary"><b>基线版本</b><span>{{ impact.base_version || '首次发布' }}</span><b>受影响术语</b><span>{{ impact.potentially_affected_terms.length }}</span></div><h4>新增三元组 · {{ impact.change_set.added.length }}</h4><pre>{{ impact.change_set.added.map(x => x.join('  ')).join('\n') || '无' }}</pre><h4>移除三元组 · {{ impact.change_set.removed.length }}</h4><pre>{{ impact.change_set.removed.map(x => x.join('  ')).join('\n') || '无' }}</pre></template>
    </el-drawer>
  </div>
</template>

<style scoped>
.governance-page { --ink:#10231f; --paper:#f3f0e8; --acid:#d8ff3e; --amber:#d98400; color:var(--ink); font-family:"Avenir Next","PingFang SC",sans-serif; }
.hero { min-height:156px; padding:28px 34px; display:flex; align-items:flex-end; justify-content:space-between; background:var(--ink); color:#f8f5ec; border-radius:2px 2px 0 0; position:relative; overflow:hidden; }
.hero::after { content:""; position:absolute; width:280px; height:280px; right:26%; top:-185px; border:1px solid rgba(216,255,62,.3); border-radius:50%; box-shadow:0 0 0 34px rgba(216,255,62,.04),0 0 0 70px rgba(216,255,62,.025); }
.eyebrow { font:600 10px/1.4 ui-monospace,SFMono-Regular,monospace; letter-spacing:.18em; color:#8faaa2; }
.hero .eyebrow { color:var(--acid); }.hero h1{font:700 35px/1.1 Georgia,"Songti SC",serif;margin:8px 0}.hero p{margin:0;color:#aebdb8;font-size:13px}.hero-actions{display:flex;gap:10px;z-index:1}.actor{width:190px}.new-button{background:var(--acid);color:var(--ink);border-color:var(--acid);font-weight:700}
.process-rail { display:grid; grid-template-columns:repeat(4,1fr); background:#e3e0d6; border-bottom:1px solid #cbc7bc; }.process-node{padding:13px 20px;color:#8b8b82;border-right:1px solid #cbc7bc;display:flex;gap:12px;align-items:center}.process-node span{font:11px ui-monospace,monospace}.process-node strong{font-size:12px}.process-node.active{background:var(--paper);color:var(--ink);box-shadow:inset 0 -3px var(--acid)}
.workbench{display:grid;grid-template-columns:230px minmax(0,1fr);min-height:650px;background:var(--paper)}.draft-rail{padding:18px 14px;border-right:1px solid #d7d2c7}.rail-title{display:flex;justify-content:space-between;font-size:12px;font-weight:700;margin:0 6px 14px}.rail-title b{background:var(--ink);color:white;border-radius:12px;padding:1px 8px}.draft-card{width:100%;border:0;border-left:3px solid transparent;background:transparent;text-align:left;padding:13px 12px;margin-bottom:5px;cursor:pointer;position:relative}.draft-card:hover{background:#ebe7dc}.draft-card.selected{background:white;border-left-color:var(--acid);box-shadow:0 4px 18px rgba(16,35,31,.07)}.draft-name{display:block;font:700 14px Georgia,serif}.draft-meta{display:block;font:9px ui-monospace,monospace;color:#7b817d;margin-top:7px;letter-spacing:.05em}.draft-card i{position:absolute;right:10px;top:14px;width:7px;height:7px;border-radius:50%;background:#999}.draft-card i.state-approved,.draft-card i.state-published,.draft-card i.state-validated{background:#4b8e5d}.draft-card i.state-conflict_review{background:#d98400}.empty-rail{text-align:center;color:#999;font-size:12px;line-height:1.8;padding-top:80px}
.editor-shell{padding:24px 28px;min-width:0}.editor-topbar{display:flex;justify-content:space-between;align-items:end;margin-bottom:10px}.mono-id{font:10px ui-monospace,monospace;color:#7a837f}.editor-topbar h2{font:700 24px Georgia,"Songti SC",serif;margin:5px 0}.editor-topbar small{font:10px ui-monospace,monospace;color:#888}.status-block{text-align:right}.status-block span{display:block;font-size:10px;color:#888}.status-block strong{font-size:14px}.semantic-tabs textarea{width:100%;height:280px;resize:vertical;border:1px solid #c7c3b9;background:#132620;color:#d7f4e7;padding:18px;font:12px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace;outline:none;tab-size:2}.semantic-tabs textarea:focus{border-color:#6d8f36;box-shadow:0 0 0 2px rgba(216,255,62,.25)}.action-strip{display:flex;gap:8px;align-items:center;padding:13px 0 20px;border-bottom:1px solid #d7d2c7}.action-spacer{flex:1}.publish-button{background:var(--ink);border-color:var(--ink);color:white}.publish-button:not(.is-disabled):hover{background:#23483f;border-color:#23483f;color:var(--acid)}
.review-panel{margin-top:20px;background:white;border:1px solid #ddd8ce}.review-heading{padding:16px 18px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #e5e1d8}.review-heading h3{font:700 18px Georgia,serif;margin:3px 0}.clean-gate{display:flex;gap:14px;align-items:center;padding:25px}.clean-gate>span{display:grid;place-items:center;width:38px;height:38px;border-radius:50%;background:var(--acid);font-weight:900}.clean-gate p{margin:4px 0 0;color:#7a817e;font-size:12px}.select-empty{display:grid;place-items:center;height:500px;color:#8b8b82}.impact-summary{display:grid;grid-template-columns:130px 1fr;gap:10px;padding:14px;background:#f4f1e8}.impact-summary b{font-size:12px}.impact-summary span{font:12px ui-monospace,monospace}.el-drawer pre{white-space:pre-wrap;background:#132620;color:#d7f4e7;padding:14px;font:11px/1.5 ui-monospace,monospace;max-height:240px;overflow:auto}
@media(max-width:1000px){.workbench{grid-template-columns:1fr}.draft-rail{display:flex;overflow:auto;border-right:0;border-bottom:1px solid #d7d2c7}.rail-title{display:none}.draft-card{min-width:170px}.hero{align-items:flex-start;gap:20px}.hero-actions{flex-direction:column}.process-node{padding:10px}.process-node span{display:none}}
</style>
