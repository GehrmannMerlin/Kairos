import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import { router } from '@/app/router'
import ModelConfigDrawer from '@/features/providers/ModelConfigDrawer.vue'
import SearchConfigDrawer from '@/features/providers/SearchConfigDrawer.vue'

vi.mock('@/features/providers/providers.api', () => ({
  fetchDefinitions: vi.fn(),
  listModelConfigs: vi.fn(),
  listSearchConfigs: vi.fn(),
  createModelConfig: vi.fn(),
  updateModelConfig: vi.fn(),
  replaceModelKey: vi.fn(),
  testModelConnection: vi.fn(),
  probeModel: vi.fn(),
  listAvailableModels: vi.fn(),
  setModelDefault: vi.fn(),
  deleteModelConfig: vi.fn(),
  createSearchConfig: vi.fn(),
  updateSearchConfig: vi.fn(),
  replaceSearchKey: vi.fn(),
  testSearchConnection: vi.fn(),
  probeSearch: vi.fn(),
  deleteSearchConfig: vi.fn(),
}))

import * as providersApi from '@/features/providers/providers.api'
import type {
  ProviderDefinitionDto,
  SearchProbeResultDto,
} from '@/features/providers/providers.api'
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

const managedDef: ProviderDefinitionDto = {
  provider_type: 'deepseek',
  display_name: 'DeepSeek',
  requires_api_key: true,
  requires_model_name: true,
  requires_base_url: false,
  default_base_url: 'https://api.deepseek.com/v1',
  protocol_family: 'openai_compatible',
  base_url_mode: 'managed',
}
const openaiDef: ProviderDefinitionDto = {
  ...managedDef,
  provider_type: 'openai',
  display_name: 'OpenAI',
  default_base_url: 'https://api.openai.com/v1',
}
const customDef: ProviderDefinitionDto = {
  provider_type: 'custom_openai_compatible',
  display_name: 'Custom OpenAI-compatible',
  requires_api_key: true,
  requires_model_name: true,
  requires_base_url: true,
  default_base_url: null,
  protocol_family: 'openai_compatible',
  base_url_mode: 'required',
}
const searchDef: ProviderDefinitionDto = {
  provider_type: 'custom_compatible_search',
  display_name: 'Custom Compatible Search',
  requires_api_key: true,
  requires_model_name: false,
  requires_base_url: true,
  default_base_url: null,
  protocol_family: 'compatible_search',
  base_url_mode: 'required',
}
const tavilySearchDef: ProviderDefinitionDto = {
  provider_type: 'tavily',
  display_name: 'Tavily',
  requires_api_key: true,
  requires_model_name: false,
  requires_base_url: false,
  default_base_url: 'https://api.tavily.com',
  protocol_family: 'tavily',
  base_url_mode: 'managed',
}

const modelDefinitions = [openaiDef, managedDef]
const searchDefinitions = [searchDef]
const searchDefsWithTavily = [tavilySearchDef, searchDef]

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.useRealTimers()
})

function mountModelsView() {
  return mount(ModelsView, { global: { plugins: [router] } })
}

async function flush(): Promise<void> {
  await new Promise((r) => setTimeout(r, 0))
}

async function clickButton(wrapper: ReturnType<typeof mount>, text: string): Promise<void> {
  const btn = wrapper.findAll('button').find((b) => b.text().trim() === text)
  expect(btn, `button "${text}"`).toBeTruthy()
  await btn!.trigger('click')
  await flush()
}

function mockLoad(): void {
  vi.mocked(providersApi.fetchDefinitions).mockResolvedValue({
    models: modelDefinitions,
    searches: searchDefinitions,
  })
  vi.mocked(providersApi.listModelConfigs).mockResolvedValue({
    configs: [],
    definitions: modelDefinitions,
  })
  vi.mocked(providersApi.listSearchConfigs).mockResolvedValue({
    configs: [],
    definitions: searchDefinitions,
  })
}

describe('ModelsView', () => {
  it('renders model config rows from the API response', async () => {
    vi.mocked(providersApi.fetchDefinitions).mockResolvedValue({
      models: modelDefinitions,
      searches: searchDefinitions,
    })
    vi.mocked(providersApi.listModelConfigs).mockResolvedValue({
      configs: [modelConfig],
      definitions: modelDefinitions,
    })
    vi.mocked(providersApi.listSearchConfigs).mockResolvedValue({
      configs: [searchConfig],
      definitions: searchDefinitions,
    })

    const wrapper = mountModelsView()
    await flush()

    expect(wrapper.text()).toContain('main')
    expect(wrapper.text()).toContain('OpenAI')
    expect(wrapper.text()).toContain('gpt-4o-mini')
  })

  it('never renders the api key: shows 已配置 instead', async () => {
    vi.mocked(providersApi.fetchDefinitions).mockResolvedValue({
      models: modelDefinitions,
      searches: searchDefinitions,
    })
    vi.mocked(providersApi.listModelConfigs).mockResolvedValue({
      configs: [{ ...modelConfig, credential_configured: true }],
      definitions: modelDefinitions,
    })
    vi.mocked(providersApi.listSearchConfigs).mockResolvedValue({
      configs: [],
      definitions: searchDefinitions,
    })

    const wrapper = mountModelsView()
    await flush()

    expect(wrapper.text()).toContain('已配置')
    expect(wrapper.text()).not.toContain('sk-')
  })

  it('新增模型 opens the AI model drawer, never the search drawer', async () => {
    mockLoad()
    const wrapper = mountModelsView()
    await flush()
    await clickButton(wrapper, '新增模型')

    expect(wrapper.text()).toContain('新增 AI 模型')
    expect(wrapper.text()).not.toContain('新增搜索服务')
    expect(wrapper.text()).not.toContain('Custom Compatible Search')
  })

  it('新增搜索服务 opens the search drawer, never the model drawer', async () => {
    mockLoad()
    const wrapper = mountModelsView()
    await flush()
    await clickButton(wrapper, '搜索服务')
    await clickButton(wrapper, '新增搜索服务')

    expect(wrapper.text()).toContain('新增搜索服务')
    expect(wrapper.text()).not.toContain('新增 AI 模型')
  })

  it('switching tabs does not leak drawer context', async () => {
    mockLoad()
    const wrapper = mountModelsView()
    await flush()

    await clickButton(wrapper, '新增模型')
    expect(wrapper.text()).toContain('新增 AI 模型')

    // close, then open the search drawer — context must not bleed across.
    await clickButton(wrapper, '取消')
    await clickButton(wrapper, '搜索服务')
    await clickButton(wrapper, '新增搜索服务')
    expect(wrapper.text()).toContain('新增搜索服务')
    expect(wrapper.text()).not.toContain('新增 AI 模型')
  })
})

describe('ModelConfigDrawer', () => {
  async function mountDrawer(defs = [openaiDef, managedDef]) {
    const wrapper = mount(ModelConfigDrawer, {
      props: { open: false, mode: 'create', config: null, definitions: defs },
    })
    await wrapper.setProps({ open: true })
    await flush()
    return wrapper
  }

  it('does not require Base URL input for managed providers', async () => {
    const wrapper = await mountDrawer([openaiDef, managedDef])
    const baseUrlLabels = wrapper.findAll('label').filter((l) => l.text().includes('Base URL'))
    expect(baseUrlLabels.length).toBe(0)
    expect(wrapper.text()).toContain('高级设置')
    expect(wrapper.text()).toContain('https://api.openai.com/v1')
  })

  it('shows a Base URL input for custom providers', async () => {
    const wrapper = await mountDrawer([customDef])
    const baseUrlLabels = wrapper.findAll('label').filter((l) => l.text().includes('Base URL'))
    expect(baseUrlLabels.length).toBe(1)
  })

  it('uses provider-supplied model IDs instead of a free-text model name', async () => {
    vi.mocked(providersApi.listAvailableModels).mockResolvedValue({
      status: 'AVAILABLE',
      models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
      resolved_base_url: 'https://api.deepseek.com/v1',
      latency_ms: 120,
      error_code: null,
      message: null,
    })
    const wrapper = await mountDrawer([openaiDef, managedDef])
    vi.useFakeTimers()

    await wrapper.find('input[type="password"]').setValue('transient-fixture-key')
    await wrapper.find('select').setValue('deepseek')
    await vi.advanceTimersByTimeAsync(300)
    await wrapper.vm.$nextTick()

    const modelLabel = wrapper.findAll('label').find((label) => label.text().includes('模型名称'))
    expect(modelLabel).toBeTruthy()
    expect(modelLabel!.find('input').exists()).toBe(false)
    expect(modelLabel!.find('select').exists()).toBe(true)
    expect(modelLabel!.findAll('option').map((option) => option.text())).toEqual([
      'deepseek-v4-flash',
      'deepseek-v4-pro',
    ])
    expect((modelLabel!.find('select').element as HTMLSelectElement).value).toBe(
      'deepseek-v4-flash',
    )
    expect(providersApi.listAvailableModels).toHaveBeenCalledWith({
      provider_type: 'deepseek',
      api_key: 'transient-fixture-key',
    })
    vi.useRealTimers()
  })

  it('editing loads the catalog through the owned config without exposing a key', async () => {
    vi.mocked(providersApi.listAvailableModels).mockResolvedValue({
      status: 'AVAILABLE',
      models: ['gpt-4o-mini', 'gpt-5-mini'],
      resolved_base_url: 'https://api.openai.com/v1',
      latency_ms: 90,
      error_code: null,
      message: null,
    })
    const wrapper = mount(ModelConfigDrawer, {
      props: {
        open: false,
        mode: 'edit',
        config: modelConfig,
        definitions: [openaiDef, managedDef],
      },
    })
    vi.useFakeTimers()
    await wrapper.setProps({ open: true })
    await vi.advanceTimersByTimeAsync(300)
    await wrapper.vm.$nextTick()

    expect(providersApi.listAvailableModels).toHaveBeenCalledWith({
      provider_type: 'openai',
      config_id: 'c1',
    })
    expect(wrapper.text()).not.toContain('fixture-key')
    const modelLabel = wrapper.findAll('label').find((label) => label.text().includes('模型名称'))
    expect(modelLabel!.findAll('option').map((option) => option.text())).toEqual([
      'gpt-4o-mini',
      'gpt-5-mini',
    ])
    vi.useRealTimers()
  })

  it('does not allow an existing credential to be reused with another Provider', async () => {
    vi.mocked(providersApi.listAvailableModels).mockResolvedValue({
      status: 'AVAILABLE',
      models: ['gpt-4o-mini'],
      resolved_base_url: 'https://api.openai.com/v1',
      latency_ms: 90,
      error_code: null,
      message: null,
    })
    const wrapper = mount(ModelConfigDrawer, {
      props: {
        open: false,
        mode: 'edit',
        config: modelConfig,
        definitions: [openaiDef, managedDef],
      },
    })
    vi.useFakeTimers()
    await wrapper.setProps({ open: true })
    await vi.advanceTimersByTimeAsync(300)
    await wrapper.vm.$nextTick()

    const providerSelect = wrapper
      .findAll('label')
      .find((label) => label.text().includes('Provider'))
    expect((providerSelect!.find('select').element as HTMLSelectElement).disabled).toBe(true)

    vi.useRealTimers()
  })

  it('does not silently keep a legacy model missing from the provider catalog', async () => {
    vi.mocked(providersApi.listAvailableModels).mockResolvedValue({
      status: 'AVAILABLE',
      models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
      resolved_base_url: 'https://api.deepseek.com/v1',
      latency_ms: 80,
      error_code: null,
      message: null,
    })
    const legacy = { ...modelConfig, provider_type: 'deepseek', model_name: 'DeepSeek' }
    const wrapper = mount(ModelConfigDrawer, {
      props: {
        open: false,
        mode: 'edit',
        config: legacy,
        definitions: [openaiDef, managedDef],
      },
    })
    vi.useFakeTimers()
    await wrapper.setProps({ open: true })
    await vi.advanceTimersByTimeAsync(300)
    await wrapper.vm.$nextTick()

    const modelLabel = wrapper.findAll('label').find((label) => label.text().includes('模型名称'))
    expect((modelLabel!.find('select').element as HTMLSelectElement).value).toBe('')
    expect(wrapper.text()).toContain('原模型已不在服务商当前目录中')
    vi.useRealTimers()
  })

  it('probe success auto-fills provider and shows latency', async () => {
    vi.mocked(providersApi.probeModel).mockResolvedValue({
      status: 'AVAILABLE',
      detection_confidence: 'HIGH',
      detected_provider: 'deepseek',
      candidates: [],
      resolved_base_url: 'https://api.deepseek.com/v1',
      latency_ms: 428,
      error_code: null,
      message: '连接成功',
      probe_method: 'fingerprint',
    })

    const wrapper = await mountDrawer([openaiDef, managedDef])
    await wrapper.find('input[type="password"]').setValue('sk-ant-abc')
    await clickButton(wrapper, '检测连接')

    const select = wrapper.find('select').element as HTMLSelectElement
    expect(select.value).toBe('deepseek')
    expect(wrapper.text()).toContain('连接成功 · DeepSeek · 428 ms')
  })

  it('probe ambiguous asks the user to pick a provider', async () => {
    vi.mocked(providersApi.probeModel).mockResolvedValue({
      status: null,
      detection_confidence: 'AMBIGUOUS',
      detected_provider: null,
      candidates: ['openai', 'deepseek'],
      resolved_base_url: null,
      latency_ms: null,
      error_code: null,
      message: '无法仅根据 API Key 唯一识别服务商，请选择 Provider 后重新测试',
      probe_method: null,
    })

    const wrapper = await mountDrawer([openaiDef, managedDef])
    await wrapper.find('input[type="password"]').setValue('sk-1234567890')
    await clickButton(wrapper, '检测连接')

    expect(wrapper.text()).toContain('请选择 Provider')
  })

  it('probe error never renders the raw secret', async () => {
    vi.mocked(providersApi.probeModel).mockResolvedValue({
      status: 'AUTH_FAILED',
      detection_confidence: 'HIGH',
      detected_provider: 'anthropic',
      candidates: [],
      resolved_base_url: 'https://api.anthropic.com',
      latency_ms: 312,
      error_code: 'HTTP_401',
      message: 'API Key 无效',
      probe_method: 'fingerprint',
    })

    const wrapper = await mountDrawer([openaiDef, managedDef])
    await wrapper.find('input[type="password"]').setValue('sk-secret-123')
    await clickButton(wrapper, '检测连接')

    expect(wrapper.text()).toContain('API Key 无效')
    expect(wrapper.text()).not.toContain('sk-secret-123')
  })
})

describe('SearchConfigDrawer', () => {
  async function mountSearchDrawer(defs = searchDefsWithTavily) {
    const wrapper = mount(SearchConfigDrawer, {
      props: { open: false, mode: 'create', config: null, definitions: defs },
    })
    await wrapper.setProps({ open: true })
    await flush()
    return wrapper
  }

  async function selectProvider(wrapper: ReturnType<typeof mount>, value: string): Promise<void> {
    await wrapper.find('select').setValue(value)
    await flush()
  }

  async function submitForm(wrapper: ReturnType<typeof mount>): Promise<void> {
    await wrapper.find('form').trigger('submit')
    await flush()
  }

  async function findButton(wrapper: ReturnType<typeof mount>, text: string) {
    const btn = wrapper.findAll('button').find((b) => b.text().trim() === text)
    expect(btn, `button "${text}"`).toBeTruthy()
    return btn!
  }

  it('selecting Tavily hides the Base URL input', async () => {
    const wrapper = await mountSearchDrawer()
    await selectProvider(wrapper, 'tavily')
    const baseUrlLabels = wrapper.findAll('label').filter((l) => l.text().includes('Base URL'))
    expect(baseUrlLabels.length).toBe(0)
  })

  it('Tavily saves with name + provider + api key (no base_url error)', async () => {
    const wrapper = await mountSearchDrawer()
    await selectProvider(wrapper, 'tavily')
    await wrapper.findAll('input')[0].setValue('Tavily')
    await wrapper.find('input[type="password"]').setValue('tvly-key-123')
    await submitForm(wrapper)

    expect(wrapper.text()).not.toContain('请填写配置名称、Provider 与 Base URL')
    expect(providersApi.createSearchConfig).toHaveBeenCalledWith({
      name: 'Tavily',
      provider_type: 'tavily',
      base_url: undefined,
      api_key: 'tvly-key-123',
    })
  })

  it('custom search shows a Base URL input', async () => {
    const wrapper = await mountSearchDrawer([searchDef])
    const baseUrlLabels = wrapper.findAll('label').filter((l) => l.text().includes('Base URL'))
    expect(baseUrlLabels.length).toBe(1)
  })

  it('custom search without Base URL blocks save', async () => {
    const wrapper = await mountSearchDrawer([searchDef])
    await wrapper.findAll('input')[0].setValue('custom')
    await wrapper.find('input[type="password"]').setValue('sk-x')
    await submitForm(wrapper)

    expect(wrapper.text()).toContain('请填写 Base URL')
    expect(providersApi.createSearchConfig).not.toHaveBeenCalled()
  })

  it('Tavily probe success shows latency and safe label', async () => {
    vi.mocked(providersApi.probeSearch).mockResolvedValue({
      status: 'AVAILABLE',
      provider_type: 'tavily',
      resolved_base_url: 'https://api.tavily.com',
      latency_ms: 286,
      error_code: null,
      message: '连接成功',
    })

    const wrapper = await mountSearchDrawer()
    await selectProvider(wrapper, 'tavily')
    await wrapper.find('input[type="password"]').setValue('tvly-key-123')
    await clickButton(wrapper, '测试连接')

    expect(wrapper.text()).toContain('连接成功 · Tavily · 286 ms')
  })

  it('Tavily probe auth failure shows a safe message, never the key', async () => {
    vi.mocked(providersApi.probeSearch).mockResolvedValue({
      status: 'AUTH_FAILED',
      provider_type: 'tavily',
      resolved_base_url: null,
      latency_ms: 90,
      error_code: 'HTTP_401',
      message: 'API Key 无效',
    })

    const wrapper = await mountSearchDrawer()
    await selectProvider(wrapper, 'tavily')
    await wrapper.find('input[type="password"]').setValue('tvly-bad-key')
    await clickButton(wrapper, '测试连接')

    expect(wrapper.text()).toContain('API Key 无效')
    expect(wrapper.text()).not.toContain('tvly-bad-key')
  })

  it('editing the API Key invalidates a previous success', async () => {
    vi.mocked(providersApi.probeSearch).mockResolvedValue({
      status: 'AVAILABLE',
      provider_type: 'tavily',
      resolved_base_url: 'https://api.tavily.com',
      latency_ms: 100,
      error_code: null,
      message: '连接成功',
    })

    const wrapper = await mountSearchDrawer()
    await selectProvider(wrapper, 'tavily')
    await wrapper.find('input[type="password"]').setValue('tvly-key-a')
    await clickButton(wrapper, '测试连接')
    expect(wrapper.text()).toContain('连接成功')

    await wrapper.find('input[type="password"]').setValue('tvly-key-b')
    expect(wrapper.text()).not.toContain('连接成功')
  })

  it('changing the Provider invalidates a previous success', async () => {
    vi.mocked(providersApi.probeSearch).mockResolvedValue({
      status: 'AVAILABLE',
      provider_type: 'tavily',
      resolved_base_url: 'https://api.tavily.com',
      latency_ms: 100,
      error_code: null,
      message: '连接成功',
    })

    const wrapper = await mountSearchDrawer()
    await selectProvider(wrapper, 'tavily')
    await wrapper.find('input[type="password"]').setValue('tvly-key')
    await clickButton(wrapper, '测试连接')
    expect(wrapper.text()).toContain('连接成功')

    await selectProvider(wrapper, 'custom_compatible_search')
    expect(wrapper.text()).not.toContain('连接成功')
  })

  it('testing state blocks re-submission', async () => {
    let resolveProbe!: (v: SearchProbeResultDto) => void
    vi.mocked(providersApi.probeSearch).mockImplementation(
      () => new Promise((resolve) => (resolveProbe = resolve)),
    )

    const wrapper = await mountSearchDrawer()
    await selectProvider(wrapper, 'tavily')
    await wrapper.find('input[type="password"]').setValue('tvly-key')

    const testBtn = await findButton(wrapper, '测试连接')
    await testBtn.trigger('click')
    await flush()

    // While in flight the button is disabled and shows 正在测试…
    expect(providersApi.probeSearch).toHaveBeenCalledTimes(1)
    const inFlight = wrapper.findAll('button').find((b) => b.text().trim() === '正在测试…')
    expect(inFlight).toBeTruthy()
    expect((inFlight!.element as HTMLButtonElement).disabled).toBe(true)

    resolveProbe({
      status: 'AVAILABLE',
      provider_type: 'tavily',
      resolved_base_url: null,
      latency_ms: 100,
      error_code: null,
      message: '连接成功',
    })
    await flush()
    expect(providersApi.probeSearch).toHaveBeenCalledTimes(1)
  })
})
