<!--
 * Copyright (C) 2026 Andrewchay
 * Use of this software is governed by the Business Source License
 * included in the LICENSE file of this repository.
 *
 * As of the Change Date specified in that file, in accordance with
 * the Business Source License, use of this software will be governed
 * by the Apache License, Version 2.0.
 -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as api from '@/api/ingest'

const sources = ref<api.KnowledgeSource[]>([]), runs = ref<api.IngestionRun[]>([])
const selected = ref(''), loading = ref(false), running = ref(''), createOpen = ref(false), stageOpen = ref(false), staging = ref(false)
const detail = ref<Awaited<ReturnType<typeof api.getIngestionRun>> | null>(null)
const stageResult = ref<api.ContinuousStageResult | null>(null)
const form = reactive({ source_id: '', source_type: 'jsonl', config: '{\n  "identity_field": "id",\n  "snapshot_mode": "full",\n  "path": "/data/knowledge/assets.jsonl"\n}' })
const stageForm = reactive({ release_id: '', parent_release: '', resources: '[]' })
const activeSource = computed(() => sources.value.find(item => item.source_id === selected.value))
const succeeded = computed(() => runs.value.filter(item => item.status === 'succeeded').length)
const failed = computed(() => runs.value.filter(item => item.status === 'failed').length)

async function refresh() {
  loading.value = true
  try { const [s, r] = await Promise.all([api.listKnowledgeSources(), api.listIngestionRuns()]); sources.value = s.sources; runs.value = r.runs; if (!selected.value && sources.value.length) selected.value = sources.value[0].source_id }
  catch (e) { ElMessage.error(e instanceof Error ? e.message : '无法读取接入控制面') }
  finally { loading.value = false }
}
async function register() {
  try { await api.registerKnowledgeSource({ source_id: form.source_id, source_type: form.source_type, config: JSON.parse(form.config) }); createOpen.value = false; selected.value = form.source_id; await refresh(); ElMessage.success('Source revision 已注册') }
  catch (e) { ElMessage.error(e instanceof Error ? e.message : '注册失败') }
}
async function ingest(id: string) {
  running.value = id
  try { const run = await api.runKnowledgeIngestion(id, `ui-${Date.now()}`); await refresh(); await showRun(run.run_id); run.status === 'succeeded' ? ElMessage.success('ChangeSet 已固化') : ElMessage.error('失败 Run 已保存，游标未推进') }
  finally { running.value = '' }
}
async function showRun(id: string) { detail.value = await api.getIngestionRun(id) }
function openStage() {
  if (!detail.value || !activeSource.value) return
  const safe = activeSource.value.source_id.toLowerCase().replace(/[^a-z0-9._-]/g, '-')
  stageForm.release_id = `${safe}-knowledge@draft`
  stageForm.resources = JSON.stringify([{ resource_id: `aof://${activeSource.value.tenant_id}/${safe}/object-type/record`, kind: 'ObjectType', name: 'record', domain: safe, owner: activeSource.value.owner, spec: { source_id: activeSource.value.source_id, identity_field: activeSource.value.config.identity_field || 'id' } }], null, 2)
  stageOpen.value = true
}
async function stage() {
  if (!detail.value) return
  staging.value = true
  try { stageResult.value = await api.stageContinuousCompilation(detail.value.run_id, { release_id: stageForm.release_id, resources: JSON.parse(stageForm.resources), ...(stageForm.parent_release ? { parent_release: stageForm.parent_release } : {}) }); stageOpen.value = false; ElMessage.success(stageResult.value.state === 'review' ? 'Proposal 已进入审批队列' : `门禁状态：${stageResult.value.state}`) }
  catch (e) { ElMessage.error(e instanceof Error ? e.message : '持续编译入队失败') }
  finally { staging.value = false }
}
onMounted(refresh)
</script>

<template><div class="continuous-page" v-loading="loading">
  <header class="signal-head"><div><span>KNOWLEDGE SUPPLY CHAIN / 知识供应链</span><h1>企业知识接入与持续编译</h1><p>每次源变化，都携带游标、快照、ChangeSet 和可重放证据进入语义发布门。</p></div><el-button @click="createOpen=true">＋ 注册受治理 Source</el-button></header>
  <section class="pulse"><div><span>ACTIVE SOURCES</span><b>{{ sources.length }}</b><small>租户隔离连接契约</small></div><div><span>SUCCEEDED</span><b>{{ succeeded }}</b><small>游标已原子推进</small></div><div><span>FAILED / BLOCKED</span><b class="alarm">{{ failed }}</b><small>保留证据 · 不自动重试</small></div><div class="replay"><span>CONTINUOUS COMPILER</span><b>REPLAY GATED</b><small>复现一致才可晋级</small></div></section>
  <section class="layout"><aside class="registry"><h3>SOURCE REGISTRY <b>{{ sources.length }}</b></h3><button v-for="source in sources" :key="source.source_id" :class="{active:selected===source.source_id}" @click="selected=source.source_id"><i :class="{live:source.cursor}"></i><div><strong>{{ source.source_id }}</strong><span>{{ source.source_type }} · {{ source.owner }}</span></div></button><p v-if="!sources.length">尚无企业 Source</p></aside>
  <main><template v-if="activeSource"><div class="source-head"><div><span>{{ activeSource.revision_id }}</span><h2>{{ activeSource.source_id }}</h2></div><el-button :loading="running===activeSource.source_id" @click="ingest(activeSource.source_id)">执行一次接入</el-button></div><div class="contract"><label>TYPE<b>{{ activeSource.source_type }}</b></label><label>CURSOR<b>{{ activeSource.cursor||'UNSTARTED' }}</b></label><label>CONFIG<b>{{ Object.keys(activeSource.config).length }} FIELDS</b></label></div><h3>不可变运行轨迹</h3><div class="runs"><button v-for="run in runs.filter(x=>x.source_id===activeSource?.source_id)" :key="run.run_id" @click="showRun(run.run_id)"><i :class="run.status"></i><div><strong>{{ run.status.toUpperCase() }}</strong><span>{{ run.run_id }}</span></div><code>{{ run.cursor_from||'∅' }} → {{ run.cursor_to }}</code><b>{{ run.record_count }}</b></button></div></template><p v-else class="empty">选择或注册一个 Source</p></main>
  <aside class="evidence"><h3>RUN EVIDENCE</h3><template v-if="detail"><em :class="detail.status">{{ detail.status }}</em><h2>{{ detail.run_id }}</h2><dl><dt>Source snapshot</dt><dd>{{ detail.source_snapshot_digest }}</dd><dt>ChangeSet</dt><dd>{{ detail.change_set_digest }}</dd><dt>Run integrity</dt><dd>{{ detail.run_digest }}</dd></dl><template v-if="detail.change_set"><h4>CHANGE SUMMARY</h4><div class="counts"><b>+{{ detail.change_set.summary.added }}</b><b>~{{ detail.change_set.summary.updated }}</b><b>−{{ detail.change_set.summary.deleted }}</b></div><h4>SCHEMA DRIFT</h4><pre>{{ JSON.stringify(detail.change_set.schema_drift,null,2) }}</pre><el-button class="stage-button" @click="openStage">送入语义发布门</el-button></template><p v-if="detail.error" class="error">{{ detail.error.type }} · {{ detail.error.message }}</p><template v-if="stageResult"><h4>CONTINUOUS COMPILE</h4><em :class="stageResult.state">{{ stageResult.state }}</em><dl><dt>Proposal</dt><dd>{{ stageResult.proposal_id || 'BLOCKED' }}</dd><dt>Candidate</dt><dd>{{ stageResult.candidate_digest || '—' }}</dd></dl></template></template><p v-else>选择 Run 查看证据</p></aside></section>
  <el-dialog v-model="createOpen" title="注册受治理知识源" width="640px"><el-form label-position="top"><el-form-item label="Source ID"><el-input v-model="form.source_id"/></el-form-item><el-form-item label="Connector"><el-select v-model="form.source_type" style="width:100%"><el-option label="JSONL file" value="jsonl"/><el-option label="SQLite table" value="sqlite"/><el-option label="HTTP JSON API" value="api"/><el-option label="Event stream" value="events"/></el-select></el-form-item><el-form-item label="非密钥配置 JSON"><el-input v-model="form.config" type="textarea" :rows="11"/></el-form-item></el-form><template #footer><el-button @click="createOpen=false">取消</el-button><el-button type="primary" :disabled="!form.source_id" @click="register">注册 revision</el-button></template></el-dialog>
  <el-dialog v-model="stageOpen" title="ChangeSet → Semantic Proposal" width="720px"><el-alert title="需要签名身份同时具备 editor 与 validator 角色；后续审批、编译、replay、发布由独立身份完成。" type="info" :closable="false"/><el-form label-position="top"><el-form-item label="Release ID"><el-input v-model="stageForm.release_id"/></el-form-item><el-form-item label="Parent release（可选）"><el-input v-model="stageForm.parent_release"/></el-form-item><el-form-item label="Semantic IR resources JSON"><el-input v-model="stageForm.resources" type="textarea" :rows="14"/></el-form-item></el-form><template #footer><el-button @click="stageOpen=false">取消</el-button><el-button type="primary" :loading="staging" @click="stage">执行映射与治理门禁</el-button></template></el-dialog>
</div></template>

<style scoped>
.continuous-page{--ink:#142421;--paper:#f1efe7;--signal:#ff5b35;color:var(--ink);font-family:"Avenir Next","PingFang SC",sans-serif}.signal-head{min-height:165px;background:var(--ink);color:#fff;padding:32px 38px;display:flex;align-items:flex-end;justify-content:space-between;position:relative;overflow:hidden}.signal-head:after{content:"";position:absolute;right:18%;top:-150px;width:370px;height:370px;background:repeating-radial-gradient(circle,transparent 0 22px,rgba(255,91,53,.13) 23px 24px)}.signal-head span{font:600 9px ui-monospace,monospace;letter-spacing:.2em;color:#ff8d72}.signal-head h1{font:700 33px Georgia,"Songti SC",serif;margin:9px 0}.signal-head p{font-size:12px;color:#a8b9b4;margin:0}.signal-head button{z-index:1;background:var(--signal);border-color:var(--signal);color:#fff}.pulse{display:grid;grid-template-columns:repeat(4,1fr);background:#fff}.pulse>div{padding:17px 22px;border-right:1px solid #d8d4ca;display:grid;grid-template-columns:1fr auto}.pulse span{font:8px ui-monospace,monospace;letter-spacing:.13em;color:#79817e}.pulse b{font:700 26px Georgia,serif}.pulse small{grid-column:1/-1;color:#929893;font-size:9px}.pulse .alarm{color:#bd3e28}.pulse .replay{background:#263b36;color:#fff}.replay b{font:700 12px ui-monospace,monospace;color:#75e5b2}.layout{display:grid;grid-template-columns:230px minmax(430px,1fr) 300px;min-height:620px;background:var(--paper)}.registry,.evidence{padding:18px 14px;background:#e8e5dc}.registry{border-right:1px solid #cec9be}.evidence{border-left:1px solid #cec9be;background:#fff}.registry h3,.evidence>h3{font:700 9px ui-monospace,monospace;letter-spacing:.13em}.registry h3{display:flex;justify-content:space-between}.registry button{width:100%;display:flex;gap:10px;border:0;background:transparent;text-align:left;padding:12px;cursor:pointer}.registry button.active{background:#fff;box-shadow:0 5px 18px #14242114}.registry i{width:8px;height:8px;border-radius:50%;background:#aaa;margin-top:4px}.registry i.live{background:#39a86b}.registry strong,.registry span{display:block}.registry strong{font:700 12px Georgia,serif}.registry span{font:8px ui-monospace,monospace;color:#7b837f;margin-top:4px}.layout main{padding:25px 28px}.source-head{display:flex;justify-content:space-between;align-items:end}.source-head span{font:8px ui-monospace,monospace;color:#7e8783}.source-head h2{font:700 25px Georgia,serif;margin:5px 0}.contract{display:grid;grid-template-columns:repeat(3,1fr);background:#fff;border:1px solid #d7d2c7;margin:16px 0 25px}.contract label{padding:13px;border-right:1px solid #dedad0;font:8px ui-monospace,monospace;color:#868c89}.contract b{display:block;color:var(--ink);font-size:9px;margin-top:5px;word-break:break-all}.runs button{width:100%;display:grid;grid-template-columns:12px 1fr 180px 40px;gap:10px;align-items:center;border:0;border-bottom:1px solid #dad6cc;background:transparent;padding:13px 8px;text-align:left}.runs i{width:8px;height:8px;border-radius:50%}.runs i.succeeded{background:#39a86b}.runs i.failed{background:var(--signal)}.runs strong,.runs span{display:block}.runs span,.runs code,.runs>b{font:8px ui-monospace,monospace;color:#767f7b}.evidence em{display:inline-block;padding:4px 8px;font:8px ui-monospace,monospace;background:#dfe5df}.evidence em.failed,.evidence em.conflict_review,.evidence em.schema_drift_review{background:#ffe0d9;color:#a8331e}.evidence h2{font:700 16px Georgia,serif;word-break:break-all}.evidence dt{font:8px ui-monospace,monospace;color:#89908d;margin-top:12px}.evidence dd{font:8px/1.5 ui-monospace,monospace;margin:3px 0;word-break:break-all}.evidence h4{font:8px ui-monospace,monospace;letter-spacing:.12em;margin-top:20px}.counts{display:flex;gap:8px}.counts b{padding:7px 12px;background:var(--paper);font:11px ui-monospace,monospace}.evidence pre{font:8px/1.5 ui-monospace,monospace;background:var(--ink);color:#bce7d4;padding:10px}.stage-button{width:100%;margin-top:12px;background:var(--signal);border-color:var(--signal);color:#fff}.error{background:#ffe0d9;color:#9a2f1b;padding:10px;font-size:10px}.empty{text-align:center;padding-top:220px;color:#929893}@media(max-width:1100px){.layout{grid-template-columns:210px 1fr}.evidence{grid-column:1/-1}.pulse{grid-template-columns:repeat(2,1fr)}}
</style>
