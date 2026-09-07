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
import * as api from '@/api/runtime'

const lane = ref<'reasoning' | 'simulation' | 'workflow'>('reasoning')
const loading = ref(false), running = ref(false)
const reasoning = ref<api.RuntimeRun[]>([]), simulations = ref<api.RuntimeRun[]>([]), workflows = ref<api.RuntimeRun[]>([])
const selected = ref<api.RuntimeRun | null>(null)
const forms = reactive({
  reasoning: JSON.stringify({ ruleset_id: 'enterprise-risk@1', program: 'requires_review(X) :- risk_signal(X).', change: { change_id: 'change-001', assertions: [{ predicate: 'risk_signal', terms: ['object:001'] }], retractions: [], source: { type: 'operator', id: 'workbench' } } }, null, 2),
  simulation: JSON.stringify({ simulation_id: 'simulation-001', tenant_id: 'replace-from-signed-principal', action_plan: {}, valid_at: new Date().toISOString(), known_at: new Date().toISOString(), ruleset_id: 'impact@1', program: 'requires_review(X) :- risk_signal(X).', field_predicates: { risk_signal: 'risk_signal' }, assertions: [], retractions: [], outcome_predicates: ['requires_review'], blocking_predicates: ['requires_review'], request_digest: 'paste-canonical-request' }, null, 2),
  workflow: JSON.stringify({ api_version: 'aof.workflow-plan/v1', tenant_id: 'replace-from-signed-principal', workflow_id: 'aof://tenant/domain/workflow/example', workflow_revision: 'sha256:...', idempotency_key: 'workflow-001', nodes: {}, dependencies: {}, execution_order: [], plan_digest: 'paste-canonical-plan' }, null, 2),
})
const activeRuns = computed(() => lane.value === 'reasoning' ? reasoning.value : lane.value === 'simulation' ? simulations.value : workflows.value)
const blocked = computed(() => [...simulations.value, ...workflows.value].filter(run => ['blocked', 'reconciliation_required', 'failed'].includes(String(run.state || run.status))).length)

async function refresh() {
  loading.value = true
  try {
    const [r, s, w] = await Promise.all([api.listReasoningRuns(), api.listSimulations(), api.listWorkflowRuns()])
    reasoning.value = r.runs; simulations.value = s.runs; workflows.value = w.runs
  } catch (e) { ElMessage.error(e instanceof Error ? e.message : '无法读取企业运行控制面') }
  finally { loading.value = false }
}
async function execute() {
  running.value = true
  try {
    const payload = JSON.parse(forms[lane.value])
    selected.value = lane.value === 'reasoning' ? await api.applyReasoning(payload) : lane.value === 'simulation' ? await api.runSimulation(payload) : await api.startWorkflow(payload, 'Start the exact reviewed WorkflowPlan from enterprise runtime workbench.')
    await refresh(); ElMessage.success(`${lane.value.toUpperCase()} checkpoint 已固化`)
  } catch (e) { ElMessage.error(e instanceof Error ? e.message : '运行失败') }
  finally { running.value = false }
}
async function replay(run: api.RuntimeRun) {
  try {
    if (lane.value === 'workflow') { selected.value = await api.advanceWorkflow(run.run_id); await refresh(); return }
    const result = lane.value === 'reasoning' ? await api.replayReasoning(run.run_id) : await api.replaySimulation(run.run_id)
    result.reproduced ? ElMessage.success('独立 replay 一致') : ElMessage.error('replay 不一致，已阻断')
  } catch (e) { ElMessage.error(e instanceof Error ? e.message : '操作失败') }
}
onMounted(refresh)
</script>

<template>
  <div class="runtime" v-loading="loading">
    <header class="command-head">
      <div class="eyebrow">AOF / DETERMINISTIC OPERATIONS GRID</div>
      <div class="headline"><div><h1>企业确定性运行室</h1><p>事实变化、反事实模拟和受控 Workflow 共享同一 Release、证据链与职责分离边界。</p></div><div class="live"><i></i> AUDIT BUS ONLINE</div></div>
      <div class="flow"><span>EVENT</span><b>→</b><span>REASON</span><b>→</b><span>SIMULATE</span><b>→</b><span>APPROVE</span><b>→</b><span>ACT / WORKFLOW</span></div>
    </header>
    <section class="telemetry">
      <div><small>REASONING RUNS</small><strong>{{ reasoning.length }}</strong><em>assert / retract / replay</em></div>
      <div><small>SIMULATIONS</small><strong>{{ simulations.length }}</strong><em>valid time × knowledge time</em></div>
      <div><small>WORKFLOW RUNS</small><strong>{{ workflows.length }}</strong><em>DAG checkpoints</em></div>
      <div class="warning"><small>BLOCKED / RECONCILE</small><strong>{{ blocked }}</strong><em>no blind retries</em></div>
    </section>
    <nav class="lanes">
      <button v-for="item in [{id:'reasoning',n:'01',t:'增量推理',d:'Truth maintenance'}, {id:'simulation',n:'02',t:'反事实模拟',d:'Impact before effects'}, {id:'workflow',n:'03',t:'WorkflowRun',d:'Approval & compensation'}]" :key="item.id" :class="{active:lane===item.id}" @click="lane=item.id as typeof lane"><b>{{ item.n }}</b><span>{{ item.t }}<small>{{ item.d }}</small></span></button>
    </nav>
    <section class="console">
      <div class="editor"><div class="panel-title"><span>CANONICAL INPUT / {{ lane.toUpperCase() }}</span><i>JSON</i></div><el-input v-model="forms[lane]" type="textarea" :rows="22" spellcheck="false"/><div class="execute"><span>Actor 与 tenant 仅取自签名 Principal；body 中同名字段不会获得信任。</span><el-button :loading="running" @click="execute">COMMIT CHECKPOINT →</el-button></div></div>
      <div class="timeline"><div class="panel-title"><span>IMMUTABLE RUN LOG</span><i>{{ activeRuns.length }}</i></div><button v-for="run in activeRuns" :key="run.run_id" @click="selected=run"><i :class="String(run.state || run.status || 'succeeded')"></i><div><strong>{{ String(run.state || run.status || 'SUCCEEDED').toUpperCase() }}</strong><small>{{ run.run_id }}</small></div><code>{{ String(run.run_digest).slice(0,22) }}…</code></button><p v-if="!activeRuns.length">NO CHECKPOINTS IN SIGNED TENANT</p></div>
      <aside class="evidence"><div class="panel-title"><span>RUN EVIDENCE</span><i>PROV</i></div><template v-if="selected"><em>{{ selected.state || selected.status || 'succeeded' }}</em><h2>{{ selected.run_id }}</h2><dl><dt>RUN DIGEST</dt><dd>{{ selected.run_digest }}</dd><dt>AUDIT DECISION</dt><dd>{{ selected.audit_decision_id || 'linked through state transition' }}</dd><dt>RELEASE / RULESET</dt><dd>{{ selected.release_id || selected.ruleset_id || '—' }}</dd></dl><pre>{{ JSON.stringify(selected, null, 2) }}</pre><el-button @click="replay(selected)">{{ lane === 'workflow' ? 'ADVANCE CHECKPOINT' : 'STRICT REPLAY' }}</el-button></template><p v-else>选择一条 Run 查看输入快照、影响和决策证据。</p></aside>
    </section>
  </div>
</template>

<style scoped>
.runtime{--coal:#0d1719;--ink:#17272a;--paper:#e8e7df;--acid:#d8ff52;--amber:#ff8b3d;color:var(--ink);font-family:"Avenir Next Condensed","PingFang SC",sans-serif;background:var(--paper);min-height:820px}.command-head{padding:27px 34px 19px;background:var(--coal);color:#f3f0e4;position:relative;overflow:hidden}.command-head:after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent 49.8%,rgba(216,255,82,.07) 50%,transparent 50.2%),linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px);background-size:94px 100%,100% 24px;pointer-events:none}.eyebrow,.panel-title,.telemetry small{font:700 9px ui-monospace,monospace;letter-spacing:.18em}.eyebrow{color:var(--acid)}.headline{display:flex;align-items:end;justify-content:space-between}.headline h1{font:700 35px Georgia,"Songti SC",serif;margin:10px 0 7px}.headline p{font-size:11px;color:#91a2a2;margin:0}.live{font:700 9px ui-monospace,monospace;letter-spacing:.13em}.live i{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--acid);box-shadow:0 0 15px var(--acid);margin-right:7px}.flow{display:flex;gap:13px;align-items:center;margin-top:24px;font:700 9px ui-monospace,monospace;color:#819192}.flow span{border:1px solid #35484a;padding:6px 10px}.flow span:nth-of-type(2),.flow span:nth-of-type(3){color:var(--acid);border-color:#6e7e38}.flow b{color:var(--amber)}.telemetry{display:grid;grid-template-columns:repeat(4,1fr);background:#f8f6ee;border-bottom:1px solid #bfc2b8}.telemetry>div{padding:14px 21px;border-right:1px solid #cfd1c7;display:grid;grid-template-columns:1fr auto}.telemetry small{color:#788180}.telemetry strong{font:700 26px Georgia,serif}.telemetry em{grid-column:1/-1;font:9px ui-monospace,monospace;color:#9da19b}.telemetry .warning{background:#36251f;color:#fff}.warning strong{color:var(--amber)}.lanes{display:grid;grid-template-columns:repeat(3,1fr);padding:20px 28px 0;gap:1px}.lanes button{border:0;border-top:3px solid #aeb3aa;background:#d7d7cf;padding:13px 16px;display:flex;gap:13px;text-align:left;cursor:pointer}.lanes button.active{background:#fff;border-color:var(--amber);box-shadow:0 -8px 22px #14242112}.lanes b{font:700 19px Georgia,serif;color:#8e9490}.lanes span{font-weight:700}.lanes small{display:block;font:8px ui-monospace,monospace;color:#7e8783;margin-top:3px}.console{display:grid;grid-template-columns:minmax(420px,1.05fr) minmax(300px,.8fr) minmax(280px,.7fr);gap:1px;margin:0 28px 28px;background:#bdc0b7;border:1px solid #bdc0b7;min-height:565px}.editor,.timeline,.evidence{background:#f8f6ee;padding:17px}.panel-title{display:flex;justify-content:space-between;margin-bottom:13px}.panel-title i{font-style:normal;color:var(--amber)}.editor :deep(textarea){font:10px/1.55 ui-monospace,SFMono-Regular,monospace;background:var(--coal);color:#cfe6da;border:0;border-radius:0;box-shadow:none}.execute{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-top:12px}.execute span{font-size:9px;color:#7e8581}.execute button,.evidence button{background:var(--amber);border-color:var(--amber);color:#161d1d;font:700 9px ui-monospace,monospace}.timeline button{width:100%;display:grid;grid-template-columns:9px 1fr auto;align-items:center;gap:10px;text-align:left;border:0;border-bottom:1px solid #d2d3ca;background:transparent;padding:12px 4px;cursor:pointer}.timeline button>i{width:7px;height:7px;border-radius:50%;background:#72b894}.timeline button>i.blocked,.timeline button>i.failed,.timeline button>i.reconciliation_required{background:var(--amber);box-shadow:0 0 8px #ff8b3d88}.timeline strong,.timeline small{display:block}.timeline strong{font:700 9px ui-monospace,monospace}.timeline small,.timeline code{font:8px ui-monospace,monospace;color:#7e8581}.timeline p,.evidence>p{font:9px ui-monospace,monospace;color:#89908c;margin-top:40px}.evidence{background:#fff}.evidence>em{font:700 8px ui-monospace,monospace;text-transform:uppercase;background:var(--acid);padding:4px 8px;font-style:normal}.evidence h2{font:700 17px Georgia,serif;word-break:break-all}.evidence dt{font:8px ui-monospace,monospace;color:#969c97;margin-top:10px}.evidence dd{font:8px/1.5 ui-monospace,monospace;margin:2px 0;word-break:break-all}.evidence pre{max-height:230px;overflow:auto;background:var(--coal);color:#a9cfc0;padding:10px;font:8px/1.45 ui-monospace,monospace;white-space:pre-wrap}@media(max-width:1200px){.console{grid-template-columns:1fr 1fr}.evidence{grid-column:1/-1}.telemetry{grid-template-columns:repeat(2,1fr)}}
</style>
