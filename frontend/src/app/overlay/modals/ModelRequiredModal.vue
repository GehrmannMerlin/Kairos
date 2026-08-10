<script setup lang="ts">
import { useRouter } from 'vue-router'

import { closeModal } from '@/app/overlay/modal.store'

// D-066：未配置可用模型时打开 Model Required Modal，引导去 /models。
// M-06 再接入 Draft 保留与「返回刚才的任务」；此处为最小可用通用 UI。
defineProps<{ payload?: unknown }>()
const router = useRouter()

function goConfigure(): void {
  closeModal()
  void router.push('/models')
}
</script>

<template>
  <div class="model-required">
    <p>尚未配置可用的 AI 模型。配置后才能开始 Agent 目标理解与规划。</p>
    <div class="model-required__actions">
      <button type="button" class="ghost" @click="closeModal">取消</button>
      <button type="button" @click="goConfigure">去配置模型</button>
    </div>
  </div>
</template>

<style scoped>
.model-required__actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 1rem;
}
button {
  padding: 0.5rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-text);
  color: var(--color-bg);
  cursor: pointer;
}
button.ghost {
  background: transparent;
  color: var(--color-text);
}
</style>
