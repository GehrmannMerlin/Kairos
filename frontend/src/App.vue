<script setup lang="ts">
import AppErrorBoundary from '@/app/error/AppErrorBoundary.vue'
import AppShell from '@/app/shell/AppShell.vue'
</script>

<template>
  <AppErrorBoundary>
    <AppShell />
  </AppErrorBoundary>
</template>
