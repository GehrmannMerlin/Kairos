import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { authStore } from '@/features/auth/useAuth'
import AppView from '@/features/app/AppView.vue'
import HomeView from '@/features/home/HomeView.vue'
import NotFoundView from '@/features/home/NotFoundView.vue'
import LoginView from '@/features/auth/LoginView.vue'
import RegisterView from '@/features/auth/RegisterView.vue'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    component: HomeView,
  },
  {
    path: '/login',
    name: 'login',
    component: LoginView,
    meta: { guestOnly: true },
  },
  {
    path: '/register',
    name: 'register',
    component: RegisterView,
    meta: { guestOnly: true },
  },
  {
    path: '/app',
    name: 'app',
    component: AppView,
    meta: { requiresAuth: true },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: NotFoundView,
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
