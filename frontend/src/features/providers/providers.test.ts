import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import { router } from '@/app/router'

vi.mock('@/features/providers/providers.api', () => ({
  fetchDefinitions: vi.fn(),
  listModelConfigs: vi.fn(),
  listSearchConfigs: vi.fn(),
  createModelConfig: vi.fn(),
}))

import * as providersApi from '@/features/providers/providers.api'
import ModelsView from '@/features/providers/ModelsView.vue'

const modelConfig = {
  config_id: 'c1',
  version: 1,
  name: 'main',
  provider_type: 'openai',
  model_name: 'gpt-4o-mini',
  base_url: null,
  credential_configured: true,
  is_default: true,
  connection_status: 'available',
  last_tested_at: '2026-08-10T00:00:00Z',
  created_at: '2026-08-10T00:00:00Z',
}

const searchConfig = {
  config_id: 's1',
  version: 1,
  name: 'my-search',
  provider_type: 'custom_compatible_search',
  base_url: 'http://search:9000',
  credential_configured: true,
  connection_status: 'available',
  last_tested_at: null,
  created_at: '2026-08-10T00:00:00Z',
}

const definitions = [
  {
    provider_type: 'openai',
    display_name: 'OpenAI',
    requires_api_key: true,
    requires_model_name: true,
    requires_base_url: false,
    default_base_url: 'https://api.openai.com/v1',
    protocol_family: 'openai_compatible',
  },
  {
    provider_type: 'custom_compatible_search',
    display_name: 'Custom Compatible Search',
    requires_api_key: true,
    requires_model_name: false,
    requires_base_url: true,
    default_base_url: null,
    protocol_family: 'compatible_search',
  },
]

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ModelsView', () => {
  it('renders model config rows from the API response', async () => {
    vi.mocked(providersApi.fetchDefinitions).mockResolvedValue({
      models: definitions,
      searches: definitions,
    })
    vi.mocked(providersApi.listModelConfigs).mockResolvedValue({
      configs: [modelConfig],
      definitions,
    })
    vi.mocked(providersApi.listSearchConfigs).mockResolvedValue({
      configs: [searchConfig],
      definitions,
    })

    const wrapper = mount(ModelsView, { global: { plugins: [router] } })
    await new Promise((r) => setTimeout(r, 0))

    expect(wrapper.text()).toContain('main')
    expect(wrapper.text()).toContain('OpenAI')
    expect(wrapper.text()).toContain('gpt-4o-mini')
  })

  it('never renders the api key: shows 已配置 instead', async () => {
    vi.mocked(providersApi.fetchDefinitions).mockResolvedValue({
      models: definitions,
      searches: definitions,
    })
    vi.mocked(providersApi.listModelConfigs).mockResolvedValue({
      configs: [{ ...modelConfig, credential_configured: true }],
      definitions,
    })
    vi.mocked(providersApi.listSearchConfigs).mockResolvedValue({ configs: [], definitions })

    const wrapper = mount(ModelsView, { global: { plugins: [router] } })
    await new Promise((r) => setTimeout(r, 0))

    expect(wrapper.text()).toContain('已配置')
    expect(wrapper.text()).not.toContain('sk-')
  })
})
