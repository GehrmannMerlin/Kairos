import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import HomeView from '@/features/home/HomeView.vue'
import NotFoundView from '@/features/home/NotFoundView.vue'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    component: HomeView,
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
