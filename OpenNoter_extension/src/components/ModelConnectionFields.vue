<script setup lang="ts">
withDefaults(defineProps<{
  apiKey?: string
  baseUrl?: string
  modelName?: string
  keyPlaceholder?: string
  addressPlaceholder?: string
  disabled?: boolean
}>(), { keyPlaceholder: 'sk-…', addressPlaceholder: 'https://api.openai.com/v1' })
const emit = defineEmits<{
  'update:apiKey': [value: string]
  'update:baseUrl': [value: string]
  'update:modelName': [value: string]
}>()
</script>

<template>
  <label class="flex items-center gap-3 text-sm">
    <span class="w-20 shrink-0 text-right text-gray-600">API Key</span>
    <input :value="apiKey" class="input min-w-0 flex-1" type="password" autocomplete="off" :placeholder="keyPlaceholder" :disabled="disabled" @input="emit('update:apiKey', ($event.target as HTMLInputElement).value)">
  </label>
  <label class="flex items-center gap-3 text-sm">
    <span class="w-20 shrink-0 text-right text-gray-600">API 地址</span>
    <input :value="baseUrl" class="input min-w-0 flex-1" :placeholder="addressPlaceholder" :disabled="disabled" @input="emit('update:baseUrl', ($event.target as HTMLInputElement).value)">
  </label>
  <label v-if="modelName !== undefined" class="flex items-center gap-3 text-sm">
    <span class="w-20 shrink-0 text-right text-gray-600">模型名称</span>
    <input :value="modelName" class="input min-w-0 flex-1" :disabled="disabled" @input="emit('update:modelName', ($event.target as HTMLInputElement).value)">
  </label>
</template>
