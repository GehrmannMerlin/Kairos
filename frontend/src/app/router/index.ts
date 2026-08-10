import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import AppLayout from '@/app/shell/AppLayout.vue'
import { authStore } from '@/features/auth/useAuth'
import AppView from '@/features/app/AppView.vue'
import LoginView from '@/features/auth/LoginView.vue'
import RegisterView from '@/features/auth/RegisterView.vue'
import NotFoundView from '@/features/home/NotFoundView.vue'
import ModelsView from '@/features/providers/ModelsView.vue'
import SettingsView from '@/features/settings/SettingsView.vue'
import TaskChatView from '@/features/tasks/TaskChatView.vue'
import TaskDataView from '@/features/tasks/TaskDataView.vue'
import TaskEvidenceView from '@/features/tasks/TaskEvidenceView.vue'
import TaskExecutionView from '@/features/tasks/TaskExecutionView.vue'
import TaskQualityView from '@/features/tasks/TaskQualityView.vue'
import TaskShell from '@/features/tasks/TaskShell.vue'
import TasksView from '@/features/tasks/TasksView.vue'
import TemplateEditView from '@/features/templates/TemplateEditView.vue'
import TemplatesView from '@/features/templates/TemplatesView.vue'

/**
 * D-048 固定 13 类页面。
 * Public：/login、/register。
 * Auth（AppLayout 内，requiresAuth）：/app、/tasks、/templates(/new|edit)、
 * /models、/settings、/tasks/:taskId/{chat,data,quality,execution,evidence/:evidenceId}。
 * 二级页面（execution / evidence）不成 Tab、不进全局导航。
 */
const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: LoginView, meta: { guestOnly: true } },
  { path: '/register', name: 'register', component: RegisterView, meta: { guestOnly: true } },
  {
    path: '/',
    component: AppLayout,
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/app' },
      { path: 'app', name: 'app', component: AppView, meta: { title: '工作台' } },
      { path: 'tasks', name: 'tasks', component: TasksView, meta: { title: '我的任务' } },
      { path: 'templates', name: 'templates', component: TemplatesView, meta: { title: '模板' } },
      {
        path: 'templates/new',
        name: 'template-new',
        component: TemplateEditView,
        meta: { title: '新建模板' },
      },
      {
        path: 'templates/:templateId/edit',
        name: 'template-edit',
        component: TemplateEditView,
        meta: { title: '编辑模板' },
      },
      { path: 'models', name: 'models', component: ModelsView, meta: { title: '模型配置' } },
      { path: 'settings', name: 'settings', component: SettingsView, meta: { title: '设置' } },
      {
        path: 'tasks/:taskId',
        component: TaskShell,
        children: [
          // D-050：Task 主体永远默认进入 Chat。
          {
            path: '',
            redirect: (to) => ({ name: 'task-chat', params: { taskId: to.params.taskId } }),
          },
          { path: 'chat', name: 'task-chat', component: TaskChatView, meta: { title: '任务对话' } },
          { path: 'data', name: 'task-data', component: TaskDataView, meta: { title: '数据' } },
          {
            path: 'quality',
            name: 'task-quality',
            component: TaskQualityView,
            meta: { title: '质量' },
          },
          {
            path: 'execution',
            name: 'task-execution',
            component: TaskExecutionView,
            meta: { title: '执行详情' },
          },
          {
            path: 'evidence/:evidenceId',
            name: 'task-evidence',
            component: TaskEvidenceView,
            meta: { title: '证据查看器' },
          },
        ],
      },
      { path: ':pathMatch(.*)*', name: 'not-found', component: NotFoundView },
    ],
  },
]

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

router.beforeEach((to) => {
  if (to.meta.requiresAuth && authStore.status.value !== 'authenticated') {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.guestOnly && authStore.status.value === 'authenticated') {
    return { name: 'app' }
  }
  return true
})
