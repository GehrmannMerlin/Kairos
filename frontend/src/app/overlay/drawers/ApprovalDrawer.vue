<script setup lang="ts">
// Approval Drawer 契约（D-057）。真实审批在 M-08 接入；
// 无真实审批数据时不生成假 Approval。
defineProps<{ payload?: unknown }>()
</script>

<template>
  <p class="muted">该审批当前不可用（M-08 接入）</p>
</template>
