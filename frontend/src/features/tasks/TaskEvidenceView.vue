<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

// 证据查看器二级页（D-056）。无真实证据或 id 不存在时为安全 Empty State；
// 不 fetch 当前网页冒充历史快照。
const route = useRoute()
const evidenceId = computed(() =>
  typeof route.params.evidenceId === 'string' ? route.params.evidenceId : '',
)
</script>

<template>
  <section class="task-workspace">
    <p class="empty">证据 #{{ evidenceId }} 不存在或无权访问</p>
  </section>
</template>
