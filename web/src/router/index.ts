/*
 * Copyright (C) 2026 Andrewchay
 * Use of this software is governed by the Business Source License
 * included in the LICENSE file of this repository.
 *
 * As of the Change Date specified in that file, in accordance with
 * the Business Source License, use of this software will be governed
 * by the Apache License, Version 2.0.
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      {
        path: '',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '资产总览', icon: 'Odometer' },
      },
      {
        path: 'ingest',
        name: 'ingest',
        component: () => import('@/views/IngestView.vue'),
        meta: { title: '持续接入', icon: 'UploadFilled' },
      },
      {
        path: 'okf',
        name: 'okf',
        component: () => import('@/views/OkfBrowseView.vue'),
        meta: { title: 'OKF 知识包', icon: 'Collection' },
      },
      {
        path: 'graph',
        name: 'graph',
        component: () => import('@/views/GraphView.vue'),
        meta: { title: '知识图谱', icon: 'Share' },
      },
      {
        path: 'ontology',
        name: 'ontology',
        component: () => import('@/views/OntologyGovernanceView.vue'),
        meta: { title: '本体治理', icon: 'Connection' },
      },
      {
        path: 'runtime',
        name: 'runtime',
        component: () => import('@/views/RuntimeView.vue'),
        meta: { title: '企业运行室', icon: 'Cpu' },
      },
      {
        path: 'agent',
        name: 'agent',
        component: () => import('@/views/AgentTestView.vue'),
        meta: { title: 'Agent 测试', icon: 'ChatDotRound' },
      },
      {
        path: 'harness',
        name: 'harness',
        component: () => import('@/views/HarnessTrainerView.vue'),
        meta: { title: 'Agent 驯化', icon: 'Trophy' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
