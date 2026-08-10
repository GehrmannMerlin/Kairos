<script setup lang="ts">
// Credential Drawer 契约（D-059）。真实凭据提供/保存在 M-10 接入；
// 本基础设施不暴露任何凭据明文。
defineProps<{ payload?: unknown }>()
</script>

<template>
  <p class="muted">网站凭据将在需要时提供（M-10 接入）</p>
</template>
