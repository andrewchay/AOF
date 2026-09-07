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
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { DataSet } from 'vis-data'
import { Network } from 'vis-network'
import * as graphApi from '@/api/graph'
import { useOkfStore } from '@/stores/okf'

const okfStore = useOkfStore()

const dataset = ref('default')
const loading = ref(false)
const nodeCount = ref(0)
const edgeCount = ref(0)
const selectedNode = ref<Record<string, any> | null>(null)

let container: HTMLElement | null = null
let network: Network | null = null

async function loadGraph() {
  loading.value = true
  try {
    const nodesData = await graphApi.getNodes(dataset.value, 200)
    const edgesData = await graphApi.getEdges(dataset.value, 500)
    nodeCount.value = nodesData.pagination?.total ?? nodesData.nodes.length
    edgeCount.value = edgesData.edges.length
    renderNetwork(nodesData.nodes, edgesData.edges)
  } catch (e: any) {
    console.warn('图谱加载失败', e)
    nodeCount.value = 0
    edgeCount.value = 0
    selectedNode.value = null
    if (container) container.innerHTML = ''
  } finally {
    loading.value = false
  }
}

function renderNetwork(nodes: any[], edges: any[]) {
  if (!container) return
  const visNodes = nodes.map((n, i) => ({
    id: String(n.id ?? `n${i}`),
    label: String(n.properties?.name ?? n.labels?.[0] ?? n.id ?? `Node ${i}`),
    group: Array.isArray(n.labels) ? (n.labels[0] ?? 'node') : (n.labels ?? 'node'),
  }))
  const visEdges = edges.map((e, i) => ({
    id: String(e.id ?? `e${i}`),
    from: String(e.source_id ?? e.source ?? ''),
    to: String(e.target_id ?? e.target ?? ''),
    label: e.relationship_type ?? e.relation ?? '',
  })).filter((e) => e.from && e.to)

  network = new Network(
    container,
    { nodes: new DataSet(visNodes), edges: new DataSet(visEdges) },
    {
      nodes: { shape: 'dot', size: 14, font: { size: 12 } },
      edges: { font: { size: 10, align: 'middle' }, arrows: { to: { enabled: true, scaleFactor: 0.5 } } },
      physics: { enabled: true, solver: 'forceAtlas2Based' },
      interaction: { hover: true },
    },
  )

  network.on('click', (params: any) => {
    const id = params?.nodes?.[0]
    if (!id) {
      selectedNode.value = null
      return
    }
    const raw = nodes.find((n) => String(n.id) === String(id))
    selectedNode.value = raw ?? null
  })

  network.on('deselectNode', () => { selectedNode.value = null })
}

onMounted(async () => {
  container = document.getElementById('graph-canvas')
  await okfStore.loadBundles()
  if (okfStore.bundles.length) {
    dataset.value = okfStore.bundles[0].name
  }
  loadGraph()
})

onBeforeUnmount(() => {
  if (network) network.destroy()
})
</script>

<template>
  <div>
    <el-card shadow="never">
      <div class="toolbar">
        <el-select v-model="dataset" style="width: 200px" @change="loadGraph">
          <el-option v-for="b in okfStore.bundles" :key="b.name" :label="b.title" :value="b.name" />
        </el-select>
        <el-tag>节点 {{ nodeCount }}</el-tag>
        <el-tag type="success">边 {{ edgeCount }}</el-tag>
        <el-button type="primary" :loading="loading" @click="loadGraph">刷新</el-button>
      </div>
    </el-card>

    <el-row :gutter="16" class="content">
      <el-col :span="18">
        <el-card shadow="never">
          <div id="graph-canvas" class="graph-canvas" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="node-panel">
          <template #header><span>节点详情</span></template>
          <el-empty v-if="!selectedNode" description="点击图谱节点查看详情" />
          <div v-else>
            <div class="node-title">{{ selectedNode.properties?.name ?? selectedNode.name ?? selectedNode.id }}</div>
            <div v-if="selectedNode.properties" class="node-props">
              <template v-for="(kv, k) in selectedNode.properties" :key="k">
                <div v-if="typeof kv !== 'object'" class="prop-row">
                  <span class="prop-key">{{ k }}</span>
                  <span class="prop-val">{{ kv }}</span>
                </div>
              </template>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
}
.content {
  margin-top: 16px;
}
.graph-canvas {
  width: 100%;
  height: 72vh;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: radial-gradient(circle at center, #f8fafc, #eef2f7);
}
.node-panel {
  height: 72vh;
  overflow: auto;
}
.node-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 12px;
}
.prop-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  border-bottom: 1px dashed #eef2f7;
  font-size: 13px;
}
.prop-key {
  color: #667085;
}
.prop-val {
  color: #0f172a;
  word-break: break-all;
}
</style>
