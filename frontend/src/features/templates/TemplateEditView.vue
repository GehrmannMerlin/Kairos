<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

// 模板编辑独立全宽页（D-054）。真正编辑业务在 M-06 接入；M-05 只建立 route contract + 页面壳。
const route = useRoute()
const templateId = computed(() =>
  typeof route.params.templateId === 'string' ? route.params.templateId : null,
)
</script>

<template>
  <section class="page page--wide">
    <header class="page__header">
      <h1>{{ templateId ? '编辑模板' : '新建模板' }}</h1>
    </header>
    <p class="muted">{{ templateId ? `模板 #${templateId}` : '新模板' }}：模板编辑将在 M-06 接入</p>
  </section>
</template>
