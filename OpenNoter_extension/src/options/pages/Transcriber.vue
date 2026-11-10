<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ModelConnectionFields from '~/components/ModelConnectionFields.vue'
import {
  downloadTranscriberModel,
  getTranscriberConfig,
  getTranscriberModelsStatus,
  setTranscriberConfig,
  updateAsrEndpoint,
} from '~/logic/api'
import type {
  TranscriberConfig,
  TranscriberModelsStatus,
  TranscriberType,
  WhisperModelSize,
  WhisperModelStatus,
} from '~/logic/types'

const config = ref<TranscriberConfig | null>(null)
const status = ref<TranscriberModelsStatus | null>(null)

const selType = ref<TranscriberType>('fast-whisper')
const selSize = ref<WhisperModelSize>('medium')

const asrBaseUrl = ref('')
const asrApiKey = ref('')
const asrModel = ref('')

const loading = ref(false)
const saving = ref(false)
const message = ref<{ kind: 'ok' | 'err' | 'idle', text: string }>({ kind: 'idle', text: '' })

const isWhisperLike = computed(() => selType.value === 'fast-whisper' || selType.value === 'mlx-whisper')

async function refresh() {
  loading.value = true
  message.value = { kind: 'idle', text: '' }
  try {
    const [cfg, st] = await Promise.all([getTranscriberConfig(), getTranscriberModelsStatus()])
    config.value = cfg
    asrBaseUrl.value = cfg.resolved_endpoint || ''
    asrModel.value = cfg.resolved_model || ''
    asrApiKey.value = ''
    status.value = st
    selType.value = cfg.transcriber_type
    if (cfg.whisper_model_size)
      selSize.value = cfg.whisper_model_size
  }
  catch (e) {
    message.value = { kind: 'err', text: `读取失败：${(e as Error).message}` }
  }
  finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  message.value = { kind: 'idle', text: '保存中…' }
  try {
    if (selType.value === 'groq') {
      if (!/^https?:\/\/.+/.test(asrBaseUrl.value.trim()))
        throw new Error('API 地址需要以 http:// 或 https:// 开头')
      await updateAsrEndpoint({ base_url: asrBaseUrl.value.trim(), api_key: asrApiKey.value.trim() || undefined, model: asrModel.value.trim() || undefined })
      asrApiKey.value = ''
    }
    await setTranscriberConfig(selType.value, isWhisperLike.value ? selSize.value : undefined)
    config.value = await getTranscriberConfig()
    message.value = { kind: 'ok', text: '已保存。下一次生成笔记会用新配置。' }
  }
  catch (e) {
    message.value = { kind: 'err', text: `保存失败：${(e as Error).message}` }
  }
  finally {
    saving.value = false
  }
}

async function triggerDownload(size: WhisperModelSize) {
  try {
    await downloadTranscriberModel(size, selType.value === 'mlx-whisper' ? 'mlx-whisper' : 'fast-whisper')
    message.value = { kind: 'ok', text: `已开始下载 ${size}` }
    status.value = await getTranscriberModelsStatus()
  }
  catch (e) {
    message.value = { kind: 'err', text: `触发下载失败：${(e as Error).message}` }
  }
}

const currentSizeStatus = computed<WhisperModelStatus[]>(() => {
  if (!status.value)
    return []
  return selType.value === 'mlx-whisper' ? status.value.mlx_whisper : status.value.whisper
})

const selectedEngine = computed(() => config.value?.available_types.find(type => type.value === selType.value))
const dirty = computed(() => config.value && (
  selType.value !== config.value.transcriber_type
  || (isWhisperLike.value && selSize.value !== config.value.whisper_model_size)
  || (selType.value === 'groq' && (asrBaseUrl.value.trim() !== (config.value.resolved_endpoint || '')
    || asrModel.value.trim() !== (config.value.resolved_model || '') || Boolean(asrApiKey.value.trim())))
))

onMounted(refresh)
</script>

<template>
  <div v-if="loading" class="p-6 text-sm text-gray-500">
    加载中…
  </div>
  <div v-else-if="config" class="p-6 flex flex-wrap gap-6">
    <aside class="w-64 shrink-0 flex flex-col gap-2">
      <h1 class="text-xl font-bold">
        音频转写
      </h1>
      <div class="bg-white border rounded" aria-label="转写引擎">
        <button
          v-for="opt in config.available_types" :key="opt.value"
          class="w-full text-left px-3 py-2 border-b last:border-b-0 hover:bg-gray-50"
          :class="{ 'bg-blue-50': opt.value === selType }"
          :aria-current="opt.value === selType ? 'true' : undefined"
          :disabled="saving" @click="selType = opt.value"
        >
          <span class="block text-sm font-medium">{{ opt.label }}</span>
          <span class="text-xs text-gray-500">{{ opt.value === config.transcriber_type ? '当前使用' : opt.value.includes('whisper') ? '本地模型' : '在线服务' }}</span>
        </button>
      </div>
    </aside>
    <main class="flex-1 min-w-0 max-w-2xl flex flex-col gap-4">
      <h2 class="text-lg font-semibold">
        {{ selectedEngine?.label }}
      </h2>
      <section class="section-card">
        <p class="text-xs text-gray-500">
          保存后对新任务生效。本地 Whisper 需要先下载模型。
        </p>
        <template v-if="selType === 'groq'">
          <p class="text-xs text-gray-500">
            支持 Groq 或其他 OpenAI 兼容的语音转写服务。
          </p>
          <ModelConnectionFields
            v-model:api-key="asrApiKey" v-model:base-url="asrBaseUrl" v-model:model-name="asrModel"
            :key-placeholder="config.has_asr_key ? '已配置，留空则不修改' : '尚未配置，请填写'"
            address-placeholder="https://api.groq.com/openai/v1" :disabled="saving"
          />
        </template>
        <label v-if="isWhisperLike" class="flex items-center gap-3 text-sm">
          <span class="w-20 shrink-0 text-right text-gray-600">模型名称</span>
          <select v-model="selSize" class="input min-w-0 flex-1" :disabled="saving">
            <option v-for="s in config.whisper_model_sizes" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <p v-if="selType === 'mlx-whisper' && !config.mlx_whisper_available" class="text-xs text-red-600">
          MLX Whisper 当前不可用：需要 macOS 并安装 mlx-whisper。
        </p>
        <div class="flex items-center justify-end gap-2 pt-2">
          <button class="btn-secondary" :disabled="saving" @click="refresh">
            刷新状态
          </button>
          <button class="btn-primary" :disabled="saving || !dirty || (selType === 'mlx-whisper' && !config.mlx_whisper_available)" @click="save">
            {{ saving ? '保存中…' : '保存修改' }}
          </button>
        </div>
      </section>
      <section v-if="isWhisperLike" class="section-card">
        <h3 class="font-semibold">
          本地模型
        </h3>
        <table class="text-sm w-full">
          <thead>
            <tr class="text-left text-gray-500">
              <th class="py-1 font-normal">
                模型
              </th><th class="py-1 font-normal">
                状态
              </th><th class="py-1 font-normal">
                操作
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in currentSizeStatus" :key="row.model_size" class="border-t">
              <td class="py-1">
                {{ row.model_size }}
              </td>
              <td class="py-1">
                <span v-if="row.downloaded" class="tag bg-green-100 text-green-700">已下载</span>
                <span v-else-if="row.downloading" class="tag bg-yellow-100 text-yellow-700">下载中…</span>
                <span v-else class="tag bg-gray-100 text-gray-500">未下载</span>
              </td>
              <td class="py-1">
                <button v-if="!row.downloaded && !row.downloading" class="btn-secondary" @click="triggerDownload(row.model_size)">
                  下载
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
      <p v-if="message.text" role="status" class="text-xs" :class="message.kind === 'err' ? 'text-red-600' : 'text-green-700'">
        {{ message.text }}
      </p>
    </main>
  </div>
  <div v-else class="p-6">
    <p role="alert" class="text-sm text-red-600">
      {{ message.text || '无法加载转写配置' }}
    </p>
    <button class="btn-secondary mt-3" @click="refresh">
      重新加载
    </button>
  </div>
</template>
