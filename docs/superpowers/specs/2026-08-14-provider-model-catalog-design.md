# Provider Model Catalog Design

## Purpose

Kairos users must not type provider model IDs from memory. Selecting a Model Provider must lead to a real, provider-supplied catalog, and the saved value must be one of the IDs returned for the selected credential and endpoint.

This closes the production incident where a DeepSeek configuration named `DeepSeek` passed a connection-only check but failed the first Goal Understanding inference.

## User experience

The model configuration drawer keeps the existing calm enterprise layout and changes only the model-selection workflow:

1. The user selects a Provider.
2. For a new configuration, Kairos loads models after the required API Key and Base URL inputs are present. The request is debounced and can also be retried explicitly.
3. For an existing configuration, Kairos loads models with the owned encrypted credential already attached to that configuration; the credential is decrypted only inside `CredentialVault.read_for_execution`.
4. “模型名称” is a select control, never a free-text input.
5. While loading, the select is disabled and shows a loading state. Empty and failed catalogs show explicit retryable states; they never fall back to sample IDs.
6. If the saved model still exists, it remains selected. If it no longer exists, the UI explains that the provider no longer reports it and requires a valid selection before save.
7. A newly loaded catalog selects the first provider-returned model only when no model has been selected yet. The user may choose another returned ID.

For the current production DeepSeek configuration, the controlled remediation will create a new config version using `deepseek-v4-flash`, run the saved-config connection test, and then run Goal Understanding. It will not change Tavily.

## Backend architecture

`ModelProvider` gains a model-catalog operation returning a typed, secret-free `ModelCatalogResult`. Every adapter calls its provider's official list endpoint:

- OpenAI, DeepSeek, OpenRouter and custom OpenAI-compatible: `GET {base_url}/models`, parse `data[].id`.
- Anthropic: `GET {base_url}/v1/models`, parse `data[].id`.
- Gemini: `GET {base_url}/models`, parse `models[].baseModelId` and retain entries supporting `generateContent`.
- Ollama: `GET {base_url}/api/tags`, parse `models[].model` or `models[].name`.

The application exposes `POST /api/providers/models/catalog`. Its command accepts `provider_type`, optional `base_url`, and exactly one credential source:

- a write-only transient `api_key` for a new configuration; or
- an owned `config_id` for an existing configuration.

The service rejects cross-user config access, validates managed/custom Base URL rules through the Registry, calls at most the explicitly selected provider, and returns only status, model IDs, resolved Base URL, stable error metadata and latency. It never returns raw provider bodies or secrets.

## Test and inference parity

Saved “测试连接” uses the same adapter catalog operation and requires the configured model ID to be present. A successful provider list response is not sufficient when the selected model is absent; the status becomes `MODEL_NOT_FOUND`.

Inference continues to use the existing real chain:

`Task → GoalUnderstandingService → ModelConfig version → CredentialVault → FunctionModel bridge → ModelInferenceClient → provider HTTP endpoint`.

HTTP 400 represents a transport-successful invalid request and maps to `PROVIDER_INFERENCE_ERROR`, not `NETWORK_ERROR`. Authentication, model-not-found, rate-limit and true network mappings remain stable.

## Security and data boundaries

- API keys remain write-only and are never included in catalog responses, logs, Temporal history, fixtures or committed files.
- An existing config credential can only be used by its owner.
- The catalog endpoint makes one request only to the provider explicitly selected by the user.
- No silent fake catalog or hardcoded production fallback is allowed.
- Catalog discovery does not persist a transient key.
- Updating the current DeepSeek model creates a normal immutable ModelConfig version; no direct database edit is used.

## Verification and release

Backend verification covers all catalog response shapes, saved model membership, credential version selection, cross-user isolation, safe error mapping and Goal Understanding persistence. Frontend verification covers provider changes, loading, empty/error/retry states, valid selection, invalid legacy selections, secret non-rendering, desktop and mobile layout.

Release follows Git/PR/CI, immutable GHCR images, Staging pull and real DeepSeek smoke, then Production pull and the same smoke. Production servers are never used to edit source or build images.
