<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as ingestApi from '@/api/ingest'
import * as assetsApi from '@/api/assets'

const activeTab = ref('batch')

// 本地目录批量摄取
const directory = ref('')
const batchDataset = ref('')
const recursive = ref(true)
const batchLoading = ref(false)
const batchResult = ref<{ status?: string; dataset_name?: string; summary?: { total_files: number; success_count: number; error_count: number } } | null>(null)

async function doBatchIngest() {
  if (!directory.value.trim()) {
    ElMessage.warning('请输入本地目录路径')
    return
  }
  batchLoading.value = true
  try {
    batchResult.value = await ingestApi.batchIngest(directory.value.trim(), batchDataset.value || undefined, recursive.value)
    if (batchResult.value.status === 'success') {
      ElMessage.success('批量摄取完成')
      datasetsLoading.value = true
      try { await loadDatasets() } finally { datasetsLoading.value = false }
    }
  } catch (e: any) {
    ElMessage.error(`摄取失败：${e.message ?? e}`)
  } finally {
    batchLoading.value = false
  }
}

// URL 摄取
const url = ref('')
const urlDataset = ref('')
const urlLoading = ref(false)
const urlResult = ref<any>(null)

async function doUrlIngest() {
  if (!url.value.trim()) {
    ElMessage.warning('请输入 URL')
    return
  }
  if (!/^https?:\/\//.test(url.value.trim())) {
    ElMessage.warning('URL 需以 http:// 或 https:// 开头')
    return
  }
  urlLoading.value = true
  try {
    urlResult.value = await ingestApi.ingestUrl(url.value.trim(), urlDataset.value || undefined)
    ElMessage.success('URL 摄取完成')
  } catch (e: any) {
    ElMessage.error(`摄取失败：${e.message ?? e}`)
  } finally {
    urlLoading.value = false
  }
}

// 数据集列表
const datasets = ref<assetsApi.DatasetInfo[]>([])
const datasetsLoading = ref(false)

async function loadDatasets() {
  try {
    const res = await assetsApi.listDatasets()
    datasets.value = res.datasets ?? []
  } catch {
    datasets.value = []
  }
}
</script>

<template>
  <div>
    <el-tabs v-model="activeTab">
      <!-- 本地目录批量摄取 -->
      <el-tab-pane label="本地目录批量摄取" name="batch">
        <el-card shadow="never">
          <el-form label-width="110px" style="max-width: 620px">
            <el-form-item label="目录路径">
              <el-input v-model="directory" placeholder="如 /data/knowledge/docs 或 /Users/me/kb" />
            </el-form-item>
            <el-form-item label="数据集名称">
              <el-input v-model="batchDataset" placeholder="留空则默认用目录名" />
            </el-form-item>
            <el-form-item label="递归子目录">
              <el-switch v-model="recursive" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="batchLoading" @click="doBatchIngest">开始摄取</el-button>
            </el-form-item>
          </el-form>

          <template v-if="batchResult">
            <el-divider />
            <el-descriptions :column="3" border title="摄取结果">
              <el-descriptions-item label="数据集">{{ batchResult.dataset_name }}</el-descriptions-item>
              <el-descriptions-item label="文件总数">{{ batchResult.summary?.total_files }}</el-descriptions-item>
              <el-descriptions-item label="成功">{{ batchResult.summary?.success_count }}</el-descriptions-item>
            </el-descriptions>
          </template>
        </el-card>
      </el-tab-pane>

      <!-- URL 摄取 -->
      <el-tab-pane label="URL 网络摄取" name="url">
        <el-card shadow="never">
          <el-form label-width="110px" style="max-width: 620px">
            <el-form-item label="URL">
              <el-input v-model="url" placeholder="https://example.com/page" />
            </el-form-item>
            <el-form-item label="数据集名称">
              <el-input v-model="urlDataset" placeholder="留空则自动命名" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="urlLoading" @click="doUrlIngest">摄取该 URL</el-button>
            </el-form-item>
          </el-form>
          <pre v-if="urlResult" class="result-pre">{{ JSON.stringify(urlResult, null, 2) }}</pre>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-card shadow="never" class="datasets-card">
      <template #header>
        <div class="card-header">
          <span>已有数据集</span>
          <el-button size="small" :loading="datasetsLoading" @click="loadDatasets">刷新</el-button>
        </div>
      </template>
      <el-table v-loading="datasetsLoading" :data="datasets" empty-text="暂无数据集">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="description" label="描述" />
        <el-table-column prop="status" label="状态" width="140" />
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.result-pre {
  background: #f8fafc;
  padding: 12px;
  border-radius: 8px;
  font-size: 12px;
  white-space: pre-wrap;
  max-height: 240px;
  overflow: auto;
}
.datasets-card {
  margin-top: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
