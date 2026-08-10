<script setup lang="ts">
// 工作台（D-045）。「+ 新任务」真实创建与最近任务真实读取在 M-06 接入；
// M-05 只呈现骨架：新任务输入为明确占位，最近任务为真实 Empty State。
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>工作台</h1>
    </header>

    <div class="card workbench__new-task">
      <textarea
        class="workbench__input"
        rows="3"
        disabled
        placeholder="描述你的采集需求，例如：帮我搜集深圳的工业自动化设备供应商，获取公司名、官网、主营产品和联系方式"
      />
      <p class="muted">任务创建能力将在下一模块接入（M-06）</p>
    </div>

    <div class="workbench__section">
      <h2 class="workbench__section-title">最近任务</h2>
      <p class="empty">暂无任务</p>
    </div>
  </section>
</template>

<style scoped>
.workbench__input {
  display: block;
  width: 100%;
  padding: 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  resize: vertical;
  background: var(--color-bg);
  color: var(--color-text-secondary);
}
.workbench__section-title {
  font-size: 1rem;
  margin: 0 0 0.75rem;
}
</style>
