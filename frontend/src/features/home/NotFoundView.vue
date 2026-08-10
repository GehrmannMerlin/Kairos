<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <section class="not-found">
    <h1>404</h1>
    <p>页面不存在</p>
    <RouterLink to="/app">返回工作台</RouterLink>
  </section>
</template>
