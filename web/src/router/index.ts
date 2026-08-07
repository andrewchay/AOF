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
        meta: { title: '知识摄取', icon: 'UploadFilled' },
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
        path: 'agent',
        name: 'agent',
        component: () => import('@/views/AgentTestView.vue'),
        meta: { title: 'Agent 测试', icon: 'ChatDotRound' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
