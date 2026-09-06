<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as harnessApi from '@/api/harness'

const sessions = ref<harnessApi.HarnessSession[]>([])
const currentSession = ref<harnessApi.HarnessSession | null>(null)
const loading = ref(false)
const creating = ref(false)

const newSession = ref({
  problem_statement: '',
  pattern_type: '',
  domain: '',
  scenario: 'business_analysis',
})

const newIteration = ref({
  agent_response: '',
  expert_score: { structure: 3, accuracy: 3, completeness: 3, style: 3, reasoning: 3 },
  expert_feedback: '',
})

async function loadSessions() {
  loading.value = true
  try {
    const res = await harnessApi.listSessions()
    sessions.value = res.sessions ?? []
  } catch (e) {
    ElMessage.error('加载会话失败')
  } finally {
    loading.value = false
  }
}

async function createSession() {
  if (!newSession.value.problem_statement.trim()) {
    ElMessage.warning('请输入问题描述')
    return
  }
  creating.value = true
  try {
    const res = await harnessApi.createSession(newSession.value)
    ElMessage.success('会话创建成功')
    sessions.value.unshift(res.session)
    newSession.value = { problem_statement: '', pattern_type: '', domain: '', scenario: 'business_analysis' }
  } catch (e) {
    ElMessage.error('创建会话失败')
  } finally {
    creating.value = false
  }
}

async function selectSession(id: string) {
  try {
    const res = await harnessApi.getSession(id)
    currentSession.value = res.session
  } catch (e) {
    ElMessage.error('加载会话详情失败')
  }
}

async function addIteration() {
  if (!currentSession.value) return
  if (!newIteration.value.agent_response.trim()) {
    ElMessage.warning('请输入 Agent 回答')
    return
  }
  try {
    const res = await harnessApi.addIteration(currentSession.value.id, {
      agent_response: newIteration.value.agent_response,
      expert_score: newIteration.value.expert_score,
      expert_feedback: newIteration.value.expert_feedback,
    })
    ElMessage.success(`迭代 ${res.iteration.number} 添加成功`)
    await selectSession(currentSession.value.id)
    newIteration.value = { agent_response: '', expert_score: { structure: 3, accuracy: 3, completeness: 3, style: 3, reasoning: 3 }, expert_feedback: '' }
  } catch (e) {
    ElMessage.error('添加迭代失败')
  }
}

async function viewAttribution() {
  if (!currentSession.value) return
  try {
    const res = await harnessApi.getAttribution(currentSession.value.id)
    // 归因报告展示在会话详情中
    if (currentSession.value) {
      currentSession.value.attribution_report = res.report
    }
  } catch (e) {
    ElMessage.error('获取归因报告失败')
  }
}

async function exportTrainingData() {
  if (!currentSession.value) return
  try {
    const res = await harnessApi.exportTrainingData(currentSession.value.id)
    ElMessage.success(`导出 ${res.sample_count} 条训练样本`)
  } catch (e) {
    ElMessage.error('导出失败')
  }
}

function scoreColor(score: number): string {
  if (score >= 4.5) return '#67C23A'
  if (score >= 4.0) return '#409EFF'
  if (score >= 3.0) return '#E6A23C'
  return '#F56C6C'
}

onMounted(loadSessions)
</script>

<template>
  <div class="harness-trainer">
    <h2>Agent 驯化工作台</h2>
    
    <div class="layout">
      <!-- 左侧：会话列表 + 创建 -->
      <div class="sidebar">
        <el-card class="create-card">
          <template #header>创建驯化会话</template>
          <el-form :model="newSession" label-position="top">
            <el-form-item label="问题描述">
              <el-input v-model="newSession.problem_statement" type="textarea" :rows="2" placeholder="例如：分析 Q3 华东区库存" />
            </el-form-item>
            <el-form-item label="模式类型">
              <el-input v-model="newSession.pattern_type" placeholder="例如：区域库存分析" />
            </el-form-item>
            <el-form-item label="场景">
              <el-select v-model="newSession.scenario" style="width: 100%">
                <el-option label="商业分析" value="business_analysis" />
                <el-option label="客服" value="customer_service" />
                <el-option label="法律" value="legal" />
              </el-select>
            </el-form-item>
            <el-button type="primary" @click="createSession" :loading="creating">创建</el-button>
          </el-form>
        </el-card>
        
        <el-card class="sessions-card">
          <template #header>会话列表</template>
          <el-table :data="sessions" v-loading="loading" size="small" @row-click="(row: harnessApi.HarnessSession) => selectSession(row.id)">
            <el-table-column prop="problem_statement" label="问题" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="80">
              <template #default="{ row }: { row: harnessApi.HarnessSession }">
                <el-tag :type="row.status === 'satisfied' ? 'success' : row.status === 'active' ? 'primary' : 'info'" size="small">
                  {{ row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="current_score" label="评分" width="70">
              <template #default="{ row }">
                <span :style="{ color: scoreColor(row.current_score) }">{{ row.current_score }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="iteration_count" label="迭代" width="60" />
          </el-table>
        </el-card>
      </div>
      
      <!-- 右侧：会话详情 -->
      <div class="main" v-if="currentSession">
        <el-card>
          <template #header>
            <div class="session-header">
              <span>会话：{{ currentSession.problem_statement }}</span>
              <el-tag :type="currentSession.status === 'satisfied' ? 'success' : 'primary'" size="small">
                {{ currentSession.status }}
              </el-tag>
            </div>
          </template>
          
          <!-- 迭代时间线 -->
          <div class="iterations">
            <h4>迭代历史</h4>
            <el-timeline>
              <el-timeline-item
                v-for="it in currentSession.iterations"
                :key="it.number"
                :type="it.is_satisfactory ? 'success' : 'primary'"
                :timestamp="`迭代 ${it.number}`"
              >
                <div class="iteration-item">
                  <div class="score-bar">
                    <span>总体评分：</span>
                    <el-rate :model-value="it.expert_score.overall" disabled :colors="['#F56C6C', '#E6A23C', '#67C23A']" />
                    <span :style="{ color: scoreColor(it.expert_score.overall), fontWeight: 'bold' }">{{ it.expert_score.overall }}</span>
                  </div>
                  <div class="response">{{ it.agent_response }}</div>
                  <div v-if="it.expert_feedback" class="feedback">反馈：{{ it.expert_feedback }}</div>
                  <div v-if="it.assets_used.length" class="assets">
                    <el-tag v-for="asset in it.assets_used" :key="asset.asset_id" size="small" type="info">
                      {{ asset.asset_name }}
                    </el-tag>
                  </div>
                </div>
              </el-timeline-item>
            </el-timeline>
          </div>
          
          <!-- 添加迭代 -->
          <div class="add-iteration" v-if="currentSession.status === 'active'">
            <h4>添加迭代</h4>
            <el-form :model="newIteration" label-position="top">
              <el-form-item label="Agent 回答">
                <el-input v-model="newIteration.agent_response" type="textarea" :rows="3" />
              </el-form-item>
              <el-form-item label="专家评分">
                <div class="score-inputs">
                  <div v-for="key in ['structure', 'accuracy', 'completeness', 'style', 'reasoning']" :key="key" class="score-item">
                    <span>{{ key }}：</span>
                    <el-slider v-model="newIteration.expert_score[key as keyof typeof newIteration.expert_score]" :min="1" :max="5" :step="1" show-stops style="width: 120px" />
                  </div>
                </div>
              </el-form-item>
              <el-form-item label="专家反馈">
                <el-input v-model="newIteration.expert_feedback" type="textarea" :rows="2" />
              </el-form-item>
              <el-button type="primary" @click="addIteration">添加迭代</el-button>
            </el-form>
          </div>
          
          <!-- 操作按钮 -->
          <div class="actions">
            <el-button @click="viewAttribution" :disabled="currentSession.iterations.length < 2">查看归因报告</el-button>
            <el-button type="success" @click="exportTrainingData" :disabled="currentSession.satisfactory_count === 0">导出训练数据</el-button>
          </div>
          
          <!-- 归因报告 -->
          <div v-if="currentSession.attribution_report" class="attribution-report">
            <h4>归因报告</h4>
            <div v-for="insight in currentSession.attribution_report.key_insights" :key="insight" class="insight">
              💡 {{ insight }}
            </div>
          </div>
        </el-card>
      </div>
      
      <div class="main empty" v-else>
        <el-empty description="选择一个会话或创建新会话" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.harness-trainer {
  padding: 20px;
}

.layout {
  display: flex;
  gap: 20px;
  margin-top: 20px;
}

.sidebar {
  width: 380px;
  flex-shrink: 0;
}

.create-card {
  margin-bottom: 16px;
}

.main {
  flex: 1;
}

.main.empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 400px;
}

.session-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.iteration-item {
  padding: 8px;
  background: #f5f7fa;
  border-radius: 4px;
}

.score-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.response {
  color: #606266;
  margin-bottom: 4px;
}

.feedback {
  color: #e6a23c;
  font-size: 12px;
  margin-bottom: 4px;
}

.assets {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.add-iteration {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid #e4e7ed;
}

.score-inputs {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}

.score-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.actions {
  margin-top: 20px;
  display: flex;
  gap: 12px;
}

.attribution-report {
  margin-top: 20px;
  padding: 12px;
  background: #f0f9ff;
  border-radius: 4px;
}

.insight {
  margin: 4px 0;
  color: #409eff;
}
</style>
