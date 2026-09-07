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
import { nextTick, onMounted, ref } from 'vue'
import { useOkfStore } from '@/stores/okf'
import * as okfApi from '@/api/okf'

const okfStore = useOkfStore()

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  sources?: { title: string; path: string; desc: string }[]
}

const question = ref('')
const messages = ref<ChatMsg[]>([])
const loading = ref(false)
const chatLog = ref<HTMLElement | null>(null)

function scrollToBottom() {
  nextTick(() => {
    if (chatLog.value) chatLog.value.scrollTop = chatLog.value.scrollHeight
  })
}

async function send() {
  const q = question.value.trim()
  if (!q || !okfStore.activeBundle) return
  messages.value.push({ role: 'user', content: q })
  question.value = ''
  scrollToBottom()
  loading.value = true
  try {
    // 基于 OKF 知识资产做语义检索，返回命中的 Concept 作为知识来源
    const res = await okfApi.searchConcepts(okfStore.activeBundle, { query: q, limit: 5 })
    const sources = (res.results as any[]).map((r) => ({ title: r.title, path: r.path, desc: r.description }))

    if (sources.length === 0) {
      messages.value.push({ role: 'assistant', content: '未在知识库中找到与该问题相关的概念。可以调整提问方式，或先通过「OKF 知识包」页面浏览知识内容。' })
    } else {
      const shown = sources.slice(0, 3).map((s) => s.desc || s.title).join('；')
      messages.value.push({
        role: 'assistant',
        content: `已在知识库中找到 ${sources.length} 个相关概念。最相关的内容：${shown}。可点击下方来源查看详情。`,
        sources,
      })
    }
  } catch (e: any) {
    messages.value.push({ role: 'assistant', content: `查询失败：${e.message ?? e}` })
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

function openSource(path: string) {
  okfStore.openConcept(path)
}

onMounted(async () => {
  await okfStore.loadBundles()
})
</script>

<template>
  <div>
    <el-card shadow="never">
      <div class="bar">
        <span>知识包：</span>
        <el-select v-model="okfStore.activeBundle" style="width: 220px" placeholder="选择知识包">
          <el-option v-for="b in okfStore.bundles" :key="b.name" :label="b.title" :value="b.name" />
        </el-select>
        <el-tag type="info" size="small">Agent 基于 OKF 知识资产检索回答</el-tag>
      </div>
    </el-card>

    <el-card shadow="never" class="chat-card">
      <div ref="chatLog" class="chat-log">
        <div v-for="(m, i) in messages" :key="i" class="chat-msg" :class="m.role">
          <el-tag size="small" :type="m.role === 'user' ? 'primary' : 'success'">
            {{ m.role === 'user' ? 'User' : 'Agent' }}
          </el-tag>
          <div class="msg-bubble">
            <div class="msg-text">{{ m.content }}</div>
            <div v-if="m.sources?.length" class="msg-sources">
              <div
                v-for="s in m.sources"
                :key="s.path"
                class="source-item"
                @click="openSource(s.path)"
              >
                <el-icon><Collection /></el-icon>
                <span class="source-title">{{ s.title }}</span>
                <span v-if="s.desc" class="source-desc">— {{ s.desc }}</span>
              </div>
            </div>
          </div>
        </div>
        <el-empty v-if="messages.length === 0" description="向 Agent 提问您的知识资产，如「TechCorp 是什么？」" />
      </div>

      <div class="chat-input">
        <el-input
          v-model="question"
          :placeholder="okfStore.activeBundle ? `针对「${okfStore.activeBundleTitle}」提问…` : '请先选择知识包'"
          :disabled="!okfStore.activeBundle"
          @keyup.enter="send"
        />
        <el-button type="primary" :loading="loading" :disabled="!okfStore.activeBundle" @click="send">发送</el-button>
      </div>
    </el-card>

    <!-- 点击来源后展示 Concept 详情 -->
    <el-card v-if="okfStore.conceptRaw" shadow="never" class="concept-card">
      <template #header><span>Concept 详情</span></template>
      <pre class="concept-pre">{{ okfStore.conceptRaw }}</pre>
    </el-card>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.chat-card {
  margin-top: 16px;
}
.chat-log {
  min-height: 340px;
  max-height: 480px;
  overflow: auto;
  background: #f8fafc;
  padding: 16px;
  border-radius: 8px;
}
.chat-msg {
  margin-bottom: 14px;
  display: flex;
  flex-direction: column;
}
.chat-msg.user {
  align-items: flex-end;
}
.chat-msg.assistant {
  align-items: flex-start;
}
.msg-bubble {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px 14px;
  margin-top: 4px;
  max-width: 80%;
}
.chat-msg.user .msg-bubble {
  background: #eff6ff;
  border-color: #bfdbfe;
}
.msg-text {
  line-height: 1.6;
  font-size: 14px;
}
.msg-sources {
  margin-top: 10px;
  border-top: 1px dashed #e2e8f0;
  padding-top: 8px;
}
.source-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  cursor: pointer;
  color: #1d4ed8;
  font-size: 13px;
}
.source-item:hover {
  text-decoration: underline;
}
.source-desc {
  color: #667085;
}
.chat-input {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
.concept-card {
  margin-top: 16px;
}
.concept-pre {
  white-space: pre-wrap;
  background: #f8fafc;
  padding: 12px;
  border-radius: 8px;
  font-size: 13px;
  max-height: 400px;
  overflow: auto;
}
</style>
