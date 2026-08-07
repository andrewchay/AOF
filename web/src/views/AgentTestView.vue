<script setup lang="ts">
import { ref } from 'vue'

const question = ref('')
const messages = ref<{ role: string; content: string }[]>([])
const loading = ref(false)

function send() {
  if (!question.value.trim()) return
  messages.value.push({ role: 'user', content: question.value })
  messages.value.push({ role: 'assistant', content: '（P3-C 阶段接入语义检索 / MCP Agent 问答，此处为骨架占位）' })
  question.value = ''
}
</script>

<template>
  <el-card shadow="never">
    <template #header><span>Agent 对话测试台</span></template>
    <div class="chat-log">
      <div
        v-for="(m, i) in messages"
        :key="i"
        class="chat-msg"
        :class="m.role"
      >
        <el-tag size="small" :type="m.role === 'user' ? 'primary' : 'info'">
          {{ m.role === 'user' ? 'User' : 'Agent' }}
        </el-tag>
        <div class="msg-text">{{ m.content }}</div>
      </div>
      <el-empty v-if="messages.length === 0" description="向 Agent 提问您的知识库" />
    </div>
    <div class="chat-input">
      <el-input
        v-model="question"
        placeholder="针对知识库提问，如“TechCorp 是做什么的？”"
        @keyup.enter="send"
      />
      <el-button type="primary" :loading="loading" @click="send">发送</el-button>
    </div>
  </el-card>
</template>

<style scoped>
.chat-log {
  min-height: 320px;
  max-height: 520px;
  overflow: auto;
  background: #f8fafc;
  padding: 12px;
  border-radius: 8px;
}
.chat-msg {
  margin-bottom: 12px;
}
.chat-msg.user {
  text-align: right;
}
.msg-text {
  background: #fff;
  border: 1px solid #e2e8f0;
  padding: 8px 12px;
  border-radius: 8px;
  display: inline-block;
  margin-top: 4px;
  text-align: left;
  max-width: 80%;
}
.chat-input {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>
