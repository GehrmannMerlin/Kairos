import { beforeEach, describe, expect, it } from 'vitest'

import { closeDrawer, drawerState, openDrawer, toggleDrawer } from '@/app/overlay/drawer.store'

describe('drawer store', () => {
  beforeEach(() => closeDrawer())

  it('opens a typed drawer with payload', () => {
    openDrawer('TASK_STATUS', { taskId: 1 })
    expect(drawerState.value.open).toBe(true)
    expect(drawerState.value.type).toBe('TASK_STATUS')
    expect(drawerState.value.payload).toEqual({ taskId: 1 })
  })

  it('closes and clears the payload', () => {
    openDrawer('APPROVAL', { approvalId: 'a1' })
    closeDrawer()
    expect(drawerState.value.open).toBe(false)
    expect(drawerState.value.type).toBeNull()
    expect(drawerState.value.payload).toBeUndefined()
  })

  it('toggles same type off and different type on', () => {
    toggleDrawer('RECORD', { recordId: 7 })
    expect(drawerState.value.type).toBe('RECORD')
    toggleDrawer('RECORD', { recordId: 7 })
    expect(drawerState.value.open).toBe(false)
    toggleDrawer('NODE_DETAIL', { nodeRunId: 3 })
    expect(drawerState.value.type).toBe('NODE_DETAIL')
  })
})
