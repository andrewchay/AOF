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
import { computed, onMounted, ref } from 'vue'
import { useOkfStore } from '@/stores/okf'
import * as okfApi from '@/api/okf'

const okfStore = useOkfStore()

const searchQuery = ref('')
const searchType = ref('')
const searchResults = ref<Record<string, any>[]>([])
const searching = ref(false)

// 解析 index.md 里按 type 分组的渐进式目录，供渲染
interface IndexEntry {
  type: string
  path: string
  title: string
  desc: string
}

const indexEntries = computed<IndexEntry[]>(() => {
  const md = okfStore.indexMd
  if (!md) return []
  const entries: IndexEntry[] = []
  let currentType = ''
  for (const line of md.split('\n')) {
    const heading = line.match(/^## (.+)$/)
    if (heading) {
      currentType = heading[1].trim()
      continue
    }
    const link = line.match(/^- \[([^\]]+)\]\(([^)]+)\)(?:\s*—\s*(.*))?$/)
    if (link) {
      entries.push({ type: currentType, path: link[2], title: link[1], desc: link[3] ?? '' })
    }
  }
  return entries
})

async function doSearch() {
  if (!okfStore.activeBundle) return
  if (!searchQuery.value && !searchType.value) {
    searchResults.value = []
    return
  }
  searching.value = true
  try {
    const res = await okfApi.searchConcepts(okfStore.activeBundle, {
      query: searchQuery.value || undefined,
      type: searchType.value || undefined,
      limit: 50,
    })
    searchResults.value = res.results as Record<string, any>[]
  } finally {
    searching.value = false
  }
}

function openConcept(path: string) {
  okfStore.openConcept(path)
}

onMounted(async () => {
  await okfStore.loadBundles()
  if (okfStore.activeBundle) {
    await okfStore.loadIndex()
    okfStore.runLint()
  }
})
</script>

<template>
  <div>
    <el-card shadow="never">
      <div class="toolbar">
        <el-select
          v-model="okfStore.activeBundle"
          placeholder="选择知识包"
          style="width: 240px"
          :loading="okfStore.loadingBundles"
          @change="okfStore.selectBundle(okfStore.activeBundle, okfStore.activeBundleTitle); okfStore.loadIndex(); okfStore.runLint()"
        >
          <el-option
            v-for="b in okfStore.bundles"
            :key="b.name"
            :label="`${b.title} (${b.concepts})`"
            :value="b.name"
          />
        </el-select>

        <div class="search-box">
          <el-input
            v-model="searchQuery"
            placeholder="搜索概念文本…"
            clearable
            @keyup.enter="doSearch"
          />
          <el-input
            v-model="searchType"
            placeholder="type 过滤（person/company/…）"
            clearable
            style="width: 180px"
            @keyup.enter="doSearch"
          />
          <el-button type="primary" :loading="searching" @click="doSearch">搜索</el-button>
          <el-button @click="searchQuery = ''; searchType = ''; searchResults = []">清空</el-button>
        </div>
      </div>
    </el-card>

    <el-row :gutter="16" class="content">
      <!-- 左：渐进式披露目录 / 搜索结果 -->
      <el-col :span="12">
        <el-card shadow="never" class="panel">
          <template #header>
            <div class="card-header">
              <span>知识索引（渐进式披露）</span>
              <el-button
                size="small"
                :loading="okfStore.loadingIndex"
                @click="okfStore.loadIndex()"
              >刷新</el-button>
            </div>
          </template>
          <template v-if="!searchQuery && !searchType">
            <div v-if="okfStore.indexMd" class="index-tree">
              <template v-for="grp in indexEntries" :key="grp.type">
                <div class="index-type">{{ grp.type }}</div>
                <div
                  v-for="e in indexEntries.filter((x) => x.type === grp.type)"
                  :key="e.path"
                  class="index-item"
                  @click="openConcept(e.path)"
                >
                  <span class="item-link">{{ e.title }}</span>
                  <span v-if="e.desc" class="item-desc">— {{ e.desc }}</span>
                </div>
              </template>
            </div>
            <el-empty v-else description="该知识包无 index" />
          </template>

          <template v-else>
            <el-table v-loading="searching" :data="searchResults" empty-text="无匹配结果">
              <el-table-column
                label="标题"
                #default="{ row }"
              >
                <a class="link" @click="openConcept(row.path)">{{ row.title }}</a>
              </el-table-column>
              <el-table-column prop="type" label="Type" width="100" />
            </el-table>
          </template>
        </el-card>
      </el-col>

      <!-- 右：Concept 详情 -->
      <el-col :span="12">
        <el-card shadow="never" class="panel">
          <template #header>
            <span>Concept 详情</span>
          </template>
          <pre v-if="okfStore.conceptRaw" class="concept-pre">{{ okfStore.conceptRaw }}</pre>
          <el-empty v-else description="点击左侧概念查看详情" />
        </el-card>
      </el-col>
    </el-row>

    <!-- Lint 体检 -->
    <el-card v-if="okfStore.lintResult" shadow="never" class="lint">
      <template #header>
        <span>知识包结构体检</span>
      </template>
      <el-tag v-if="okfStore.lintResult.error_count === 0 && okfStore.lintResult.warning_count === 0" type="success">
        ✅ 无错误、无警告
      </el-tag>
      <el-tag v-else type="warning">错误 {{ okfStore.lintResult.error_count }} / 警告 {{ okfStore.lintResult.warning_count }}</el-tag>
      <el-table v-if="okfStore.lintResult.issues.length" :data="okfStore.lintResult.issues" size="small" class="lint-table">
        <el-table-column prop="severity" label="级别" width="80" />
        <el-table-column prop="category" label="类别" width="120" />
        <el-table-column prop="message" label="说明" />
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.search-box {
  display: flex;
  gap: 8px;
}
.content {
  margin-top: 16px;
}
.panel {
  min-height: 420px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.index-tree {
  max-height: 480px;
  overflow: auto;
}
.index-type {
  font-weight: 600;
  color: #0284c7;
  margin: 12px 0 4px;
  text-transform: capitalize;
}
.index-item {
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  line-height: 1.6;
}
.index-item:hover {
  background: #eff6ff;
}
.item-link {
  color: #1d4ed8;
}
.item-desc {
  color: #667085;
}
.link {
  color: #1d4ed8;
  cursor: pointer;
}
.concept-pre {
  white-space: pre-wrap;
  background: #f8fafc;
  padding: 12px;
  border-radius: 8px;
  font-size: 13px;
  max-height: 480px;
  overflow: auto;
}
.lint {
  margin-top: 16px;
}
.lint-table {
  margin-top: 12px;
}
</style>
