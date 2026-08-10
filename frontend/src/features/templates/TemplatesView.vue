<script setup lang="ts">
// 模板列表（D-047）。Template 数据模型与业务在 M-06 接入；
// 不本地数组模拟、不以 localStorage 冒充模板 DB。
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>模板</h1>
    </header>
    <div class="toolbar">
      <button type="button" disabled title="模板编辑将在 M-06 接入">＋ 新建模板</button>
    </div>
    <p class="empty">模板功能将在后续模块接入（M-06）</p>
  </section>
</template>

<style scoped>
button {
  padding: 0.5rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  cursor: not-allowed;
  opacity: 0.6;
}
</style>
