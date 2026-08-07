<script setup lang="ts">
import { onMounted, ref } from 'vue'
import * as assetsApi from '@/api/assets'
import { useOkfStore } from '@/stores/okf'
import { useRouter } from 'vue-router'

const router = useRouter()
const okfStore = useOkfStore()

const datasets = ref<assetsApi.DatasetInfo[]>([])
const datasetCount = ref(0)
const graphStats = ref<Record<string, unknown>>({})
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const res = await assetsApi.listDatasets()
    datasets.value = res.datasets ?? []
    datasetCount.value = datasets.value.length
    try {
      graphStats.value = await assetsApi.getGraphStatistics()
    } catch {
      /* 图统计不可用时忽略 */
    }
    await okfStore.loadBundles()
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="metric">
            <div class="metric-label">数据集</div>
            <div class="metric-value">{{ datasetCount }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="metric">
            <div class="metric-label">OKF 知识包</div>
            <div class="metric-value">{{ okfStore.bundles.length }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="metric">
            <div class="metric-label">图谱节点</div>
            <div class="metric-value">
              {{ (graphStats as Record<string, any>).node_count ?? '—' }}
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="metric">
            <div class="metric-label">图谱边</div>
            <div class="metric-value">
              {{ (graphStats as Record<string, any>).edge_count ?? '—' }}
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card v-loading="loading" class="section" shadow="never">
      <template #header>
        <div class="card-header">
          <span>数据集</span>
          <el-button size="small" type="primary" @click="router.push('/ingest')">＋ 新建摄取</el-button>
        </div>
      </template>
      <el-table :data="datasets" empty-text="暂无数据集">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="description" label="描述" />
        <el-table-column prop="status" label="状态" width="120" />
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.metric {
  text-align: center;
  padding: 8px 0;
}
.metric-label {
  color: #667085;
  font-size: 13px;
}
.metric-value {
  font-size: 28px;
  font-weight: 700;
  margin-top: 8px;
}
.section {
  margin-top: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
