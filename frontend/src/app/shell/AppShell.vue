<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="shell">
    <header class="shell__header">
      <span class="shell__brand">Kairos</span>
      <nav class="shell__nav">
        <RouterLink to="/">工作台</RouterLink>
      </nav>
    </header>
    <main class="shell__main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}
.shell__header {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  padding: 0 1.5rem;
  height: 3.5rem;
  border-bottom: 1px solid var(--color-border);
}
.shell__brand {
  font-weight: 700;
  font-size: 1.05rem;
}
.shell__nav a {
  color: var(--color-text-secondary);
  text-decoration: none;
}
.shell__main {
  flex: 1;
  padding: 1.5rem;
}
</style>
