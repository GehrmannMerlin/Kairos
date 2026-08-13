<script setup lang="ts">
import { computed } from 'vue'

import { can } from '@/app/actions/allowedActions'

const props = defineProps<{
  action: string
  allowed: readonly string[]
}>()

const visible = computed(() => can(props.action, props.allowed))
</script>

<template>
  <slot v-if="visible" />
</template>
