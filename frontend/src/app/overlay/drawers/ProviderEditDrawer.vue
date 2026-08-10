<script setup lang="ts">
// Provider 编辑复用 M-03 的 ModelConfigDrawer/SearchConfigDrawer（在 /models 页），
// 不创建第二套；此 Host 槽仅登记类型并提供引导。
defineProps<{ payload?: unknown }>()
</script>

<template>
  <p class="muted">Provider 编辑在「模型配置」页完成</p>
</template>
