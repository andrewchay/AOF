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
import { useRoute, useRouter } from 'vue-router'
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { beginLogin, completeLogin, consumeReturnTo, isLoggedIn, logout } from '@/api/oidc'

const route = useRoute()
const router = useRouter()
const authenticated = ref(isLoggedIn())

const menuItems = [
  { path: '/', title: '资产总览', icon: 'Odometer' },
  { path: '/ingest', title: '持续接入', icon: 'UploadFilled' },
  { path: '/okf', title: 'OKF 知识包', icon: 'Collection' },
  { path: '/graph', title: '知识图谱', icon: 'Share' },
  { path: '/ontology', title: '本体治理', icon: 'Connection' },
  { path: '/runtime', title: '企业运行室', icon: 'Cpu' },
  { path: '/agent', title: 'Agent 测试', icon: 'ChatDotRound' },
]

const activePath = computed(() => route.path)
const currentTitle = computed(() => menuItems.find((m) => m.path === route.path)?.title ?? 'AOF')

function navigate(path: string) {
  router.push(path)
}

async function signIn() {
  await beginLogin(window.location.href)
}

function signOut() {
  logout()
  authenticated.value = false
}

onMounted(async () => {
  const callback = new URL(window.location.href)
  if (!callback.searchParams.has('code')) return
  try {
    await completeLogin(callback.href)
    authenticated.value = true
    window.history.replaceState({}, '', consumeReturnTo())
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'OIDC 登录失败')
  }
})
</script>

<template>
  <el-container class="app-shell">
    <el-aside width="220px" class="app-aside">
      <div class="app-logo">
        <span class="logo-mark">A</span>
        <span class="logo-text">AOF Knowledge OS</span>
      </div>
      <el-menu :default-active="activePath" class="app-menu" @select="navigate">
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="app-header">
        <div class="header-title">{{ currentTitle }}</div>
        <div class="header-right">
          <el-tag size="small" type="info">Agent-Ready Knowledge Asset Engine</el-tag>
          <el-button v-if="authenticated" size="small" @click="signOut">退出登录</el-button>
          <el-button v-else size="small" type="primary" @click="signIn">企业账号登录</el-button>
        </div>
      </el-header>

      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-shell {
  height: 100vh;
}
.app-aside {
  background: #001529;
  color: #fff;
  display: flex;
  flex-direction: column;
}
.app-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 16px;
  color: #fff;
  font-weight: 600;
  font-size: 15px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.logo-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: linear-gradient(135deg, #3b82f6, #06b6d4);
  font-size: 15px;
}
.logo-text {
  white-space: nowrap;
}
.app-menu {
  flex: 1;
  border-right: none;
  background: transparent;
  --el-menu-text-color: rgba(255, 255, 255, 0.72);
  --el-menu-hover-bg-color: rgba(255, 255, 255, 0.1);
  --el-menu-active-color: #fff;
}
.app-menu .el-menu-item {
  color: var(--el-menu-text-color);
}
.app-menu .el-menu-item.is-active {
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.4), rgba(6, 182, 212, 0.3));
  color: #fff;
}
.app-header {
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-title {
  font-size: 16px;
  font-weight: 600;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.app-main {
  background: #f5f7fa;
  padding: 20px;
}
</style>
