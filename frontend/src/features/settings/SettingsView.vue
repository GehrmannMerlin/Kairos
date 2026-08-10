<script setup lang="ts">
// 设置四区（D-052）。真实账户/安全/默认值/存储能力在 M-05 后续任务接入；
// 此处为静态骨架，禁止伪造开关。
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>设置</h1>
    </header>

    <section class="settings__section">
      <h2>账户资料</h2>
      <p class="muted">邮箱、显示名称（M-05 后续接入）</p>
    </section>

    <section class="settings__section">
      <h2>安全</h2>
      <p class="muted">修改密码、当前会话、退出其他设备、退出登录、已保存网站凭据（后续接入）</p>
    </section>

    <section class="settings__section">
      <h2>采集默认值</h2>
      <p class="muted">默认模型、字段扩展默认行为、高级运行默认值（后续接入）</p>
    </section>

    <section class="settings__section">
      <h2>存储与数据</h2>
      <p class="muted">任务 / 记录 / 证据 / 导出统计、清理、删除全部数据等危险操作（后续接入）</p>
    </section>
  </section>
</template>
