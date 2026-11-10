import { TextField } from '@react-spectrum/s2'
import { style } from '@react-spectrum/s2/style' with { type: 'macro' }

const fullWidth = style({ width: 'full' })

/** Shared connection controls for AI models and OpenAI-compatible ASR services. */
export function ModelConnectionFields({
  apiKey,
  onApiKeyChange,
  baseUrl,
  onBaseUrlChange,
  keyPlaceholder = 'sk-…',
  addressPlaceholder = 'https://api.openai.com/v1',
  modelName,
  onModelNameChange,
  modelPlaceholder,
  isDisabled = false,
}: {
  apiKey: string
  onApiKeyChange: (value: string) => void
  baseUrl: string
  onBaseUrlChange: (value: string) => void
  keyPlaceholder?: string
  addressPlaceholder?: string
  modelName?: string
  onModelNameChange?: (value: string) => void
  modelPlaceholder?: string
  isDisabled?: boolean
}) {
  return (
    <>
      <TextField
        label="API Key"
        type="password"
        autoComplete="off"
        value={apiKey}
        onChange={onApiKeyChange}
        placeholder={keyPlaceholder}
        isDisabled={isDisabled}
        styles={fullWidth}
      />
      <TextField
        label="API 地址"
        value={baseUrl}
        onChange={onBaseUrlChange}
        placeholder={addressPlaceholder}
        isRequired
        isDisabled={isDisabled}
        styles={fullWidth}
      />
      {modelName !== undefined && (
        <TextField
          label="模型名称"
          value={modelName}
          onChange={onModelNameChange}
          placeholder={modelPlaceholder}
          isDisabled={isDisabled}
          styles={fullWidth}
        />
      )}
    </>
  )
}
