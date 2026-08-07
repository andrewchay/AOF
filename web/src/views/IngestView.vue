<script setup lang="ts">
import { ref } from 'vue'

const datasetName = ref('')
const loading = ref(false)
</script>

<template>
  <div>
    <el-card shadow="never">
      <template #header><span>知识摄取向导</span></template>
      <el-form label-width="100px" style="max-width: 520px">
        <el-form-item label="数据集名称">
          <el-input v-model="datasetName" placeholder="如 genshin_kg" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading">开始摄取（待接线）</el-button>
          <span class="hint">P3-C 阶段接入 /v1/ingest，此处为骨架占位</span>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.hint {
  color: #94a3b8;
  font-size: 12px;
  margin-left: 12px;
}
</style>
