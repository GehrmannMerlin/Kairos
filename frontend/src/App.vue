<script setup lang="ts">
import { RouterView } from 'vue-router'

import AppErrorBoundary from '@/app/error/AppErrorBoundary.vue'
</script>

<template>
  <AppErrorBoundary>
    <RouterView />
  </AppErrorBoundary>
</template>
