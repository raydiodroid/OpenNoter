<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ModelConnectionFields from '~/components/ModelConnectionFields.vue'
import AsrMark from '~/components/AsrMark.vue'
import { ASR_ICONS } from '~/logic/asr-icons'
import { createAsrProvider, deleteAsrProvider, downloadTranscriberModel, getTranscriberConfig, getTranscriberModelsStatus, setTranscriberConfig, updateAsrProvider } from '~/logic/api'
import type { AsrProvider, TranscriberConfig, TranscriberModelsStatus, TranscriberType, WhisperModelSize, WhisperModelStatus } from '~/logic/types'

const config = ref<TranscriberConfig | null>(null)
const status = ref<TranscriberModelsStatus | null>(null)
const selType = ref<TranscriberType | 'new'>('fast-whisper')
const selSize = ref<WhisperModelSize>('medium')
const providerName = ref('')
const providerLogo = ref('cloud')
const asrBaseUrl = ref('')
const asrApiKey = ref('')
const asrModel = ref('')
const loading = ref(false)
const saving = ref(false)
const confirmDelete = ref(false)
const message = ref<{ kind: 'ok' | 'err' | 'idle', text: string }>({ kind: 'idle', text: '' })
const selectedProvider = computed(() => config.value?.providers.find(p => p.id === selType.value))
const isCreate = computed(() => selType.value === 'new')
const isOnline = computed(() => isCreate.value || selectedProvider.value?.engine === 'groq')
const isWhisperLike = computed(() => selType.value === 'fast-whisper' || selType.value === 'mlx-whisper')
const dirty = computed(() => isCreate.value || providerName.value.trim() !== selectedProvider.value?.name || providerLogo.value !== selectedProvider.value?.logo
  || (isWhisperLike.value && selSize.value !== config.value?.whisper_model_size)
  || (isOnline.value && (asrBaseUrl.value.trim() !== selectedProvider.value?.base_url || asrModel.value.trim() !== selectedProvider.value?.model_name || Boolean(asrApiKey.value.trim()))))

function select(provider: AsrProvider) {
  selType.value = provider.id
  providerName.value = provider.name
  providerLogo.value = provider.logo
  asrBaseUrl.value = provider.base_url
  asrModel.value = provider.model_name
  asrApiKey.value = ''
  confirmDelete.value = false
  message.value = { kind: 'idle', text: '' }
}
function startCreate() {
  selType.value = 'new'
  providerName.value = ''
  providerLogo.value = 'cloud'
  asrBaseUrl.value = ''
  asrModel.value = ''
  asrApiKey.value = ''
  confirmDelete.value = false
}
async function refresh() {
  loading.value = true
  try {
    const [cfg, st] = await Promise.all([getTranscriberConfig(), getTranscriberModelsStatus()])
    config.value = cfg
    status.value = st
    select(cfg.providers.find(p => p.id === cfg.transcriber_type) || cfg.providers[0])
    if (cfg.whisper_model_size)
      selSize.value = cfg.whisper_model_size
  }
  catch (e) { message.value = { kind: 'err', text: `读取失败：${(e as Error).message}` } }
  finally { loading.value = false }
}
async function save() {
  saving.value = true
  try {
    if (!providerName.value.trim())
      throw new Error('请填写供应商名称')
    if (isOnline.value && (!/^https?:\/\/.+/.test(asrBaseUrl.value.trim()) || !asrModel.value.trim()))
      throw new Error('请填写有效的 API 地址和模型名称')
    const details = { name: providerName.value.trim(), logo: providerLogo.value }
    const connection = { base_url: asrBaseUrl.value.trim(), model_name: asrModel.value.trim(), api_key: asrApiKey.value.trim() || undefined }
    const provider = isCreate.value ? await createAsrProvider({ ...details, ...connection }) : await updateAsrProvider(selType.value, { ...details, ...(isOnline.value ? connection : {}) })
    if (isWhisperLike.value && selSize.value !== config.value?.whisper_model_size)
      await setTranscriberConfig(config.value!.transcriber_type, selSize.value)
    select(provider)
    config.value = await getTranscriberConfig()
    message.value = { kind: 'ok', text: '已保存。启用的供应商可设为默认使用。' }
  }
  catch (e) { message.value = { kind: 'err', text: `保存失败：${(e as Error).message}` } }
  finally { saving.value = false }
}
async function setDefault() {
  if (selType.value === 'new')
    return
  saving.value = true
  try {
    await setTranscriberConfig(selType.value, isWhisperLike.value ? selSize.value : undefined)
    config.value = await getTranscriberConfig()
    message.value = { kind: 'ok', text: '默认供应商已更新，本地模型须下载后才能转写。' }
  }
  catch (e) { message.value = { kind: 'err', text: `切换失败：${(e as Error).message}` } }
  finally { saving.value = false }
}
async function toggle(provider: AsrProvider) {
  saving.value = true
  try {
    await updateAsrProvider(provider.id, { enabled: !provider.enabled })
    config.value = await getTranscriberConfig()
    message.value = { kind: 'ok', text: '启用状态已更新。停用默认供应商后，请选择其他默认供应商。' }
  }
  catch (e) { message.value = { kind: 'err', text: `切换失败：${(e as Error).message}` } }
  finally { saving.value = false }
}
async function remove() {
  saving.value = true
  try {
    await deleteAsrProvider(selType.value)
    await refresh()
  }
  catch (e) { message.value = { kind: 'err', text: `删除失败：${(e as Error).message}` } }
  finally { saving.value = false }
}
async function triggerDownload(size: WhisperModelSize) {
  try {
    await downloadTranscriberModel(size, selType.value === 'mlx-whisper' ? 'mlx-whisper' : 'fast-whisper')
    message.value = { kind: 'ok', text: `已开始下载 ${size}` }
    status.value = await getTranscriberModelsStatus()
  }
  catch (e) { message.value = { kind: 'err', text: `下载失败：${(e as Error).message}` } }
}
const currentSizeStatus = computed<WhisperModelStatus[]>(() => !status.value ? [] : selType.value === 'mlx-whisper' ? status.value.mlx_whisper : status.value.whisper)
onMounted(refresh)
</script>

<template>
  <div v-if="loading" class="p-6 text-sm text-gray-500">
    加载中…
  </div>
  <div v-else-if="config" class="p-6 flex flex-wrap gap-6">
    <aside class="w-72 shrink-0 flex flex-col gap-2">
      <h1 class="text-xl font-bold">
        音频转写
      </h1>
      <button class="btn-secondary w-full" :disabled="saving" @click="startCreate">
        添加供应商
      </button>
      <div class="bg-white border rounded" aria-label="转写供应商">
        <div v-for="provider in config.providers" :key="provider.id" class="flex items-center border-b last:border-b-0" :class="{ 'bg-blue-50': provider.id === selType }">
          <button class="flex-1 min-w-0 text-left px-3 py-2 flex items-center gap-2" :aria-current="provider.id === selType ? 'true' : undefined" :disabled="saving" @click="select(provider)">
            <AsrMark :name="provider.logo" />
            <span class="min-w-0"><span class="block text-sm font-medium truncate">{{ provider.name }}</span><span class="text-xs text-gray-500">{{ provider.id === config.transcriber_type ? '默认使用' : provider.engine === 'groq' ? '在线服务' : '内置引擎' }}{{ !provider.enabled ? ' · 已停用' : '' }}</span></span>
          </button>
          <button role="switch" :aria-label="`启用 ${provider.name}`" :aria-checked="provider.enabled" :disabled="saving" class="relative w-8 h-5 rounded-full mr-2 shrink-0" :class="provider.enabled ? 'bg-blue-600' : 'bg-gray-300'" @click="toggle(provider)">
            <span class="absolute top-0.5 w-4 h-4 bg-white rounded-full" :class="provider.enabled ? 'right-0.5' : 'left-0.5'" />
          </button>
        </div>
      </div>
    </aside>
    <main class="flex-1 min-w-0 max-w-2xl flex flex-col gap-4">
      <h2 class="text-lg font-semibold flex items-center gap-2">
        <AsrMark :name="providerLogo" />{{ isCreate ? '添加供应商' : selectedProvider?.name }}
      </h2>
      <section class="section-card">
        <p class="text-xs text-gray-500">
          可添加多家服务，启用后设为默认。个人 ASR 凭据仍按站点策略优先生效。
        </p>
        <label class="flex items-center gap-3 text-sm"><span class="w-20 shrink-0 text-right text-gray-600">供应商名称</span><input v-model="providerName" class="input min-w-0 flex-1" :disabled="saving"></label>
        <label class="flex items-center gap-3 text-sm"><span class="w-20 shrink-0 text-right text-gray-600">图标</span><select v-model="providerLogo" aria-label="图标" class="input min-w-0 flex-1" :disabled="saving"><option v-for="icon in ASR_ICONS" :key="icon.id" :value="icon.id">{{ icon.label }}</option></select></label>
        <p v-if="selectedProvider && !selectedProvider.enabled" class="text-xs text-orange-700">
          此供应商已停用，启用后才能用于转写。
        </p>
        <ModelConnectionFields v-if="isOnline" v-model:api-key="asrApiKey" v-model:base-url="asrBaseUrl" v-model:model-name="asrModel" :key-placeholder="selectedProvider?.has_api_key ? '已配置，留空则不修改' : '尚未配置，请填写'" address-placeholder="https://api.groq.com/openai/v1" :disabled="saving" />
        <label v-if="isWhisperLike" class="flex items-center gap-3 text-sm"><span class="w-20 shrink-0 text-right text-gray-600">模型名称</span><select v-model="selSize" class="input min-w-0 flex-1" :disabled="saving"><option v-for="s in config.whisper_model_sizes" :key="s" :value="s">{{ s }}</option></select></label>
        <p v-if="selType === 'mlx-whisper' && !config.mlx_whisper_available" class="text-xs text-red-600">
          MLX Whisper 当前不可用：需要 macOS 并安装 mlx-whisper。
        </p>
        <div class="flex items-center justify-end gap-2 pt-2 flex-wrap">
          <button class="btn-secondary" :disabled="saving" @click="refresh">
            刷新状态
          </button>
          <button class="btn-secondary" :disabled="saving || isCreate || dirty || !selectedProvider?.enabled || selType === config.transcriber_type || (selType === 'mlx-whisper' && !config.mlx_whisper_available)" @click="setDefault">
            {{ selType === config.transcriber_type ? '默认使用' : '设为默认' }}
          </button>
          <button v-if="selectedProvider && !selectedProvider.builtin" class="btn-secondary text-red-600" :disabled="saving || selType === config.transcriber_type" @click="confirmDelete = true">
            删除供应商
          </button>
          <button class="btn-primary" :disabled="saving || !dirty" @click="save">
            {{ saving ? '保存中…' : isCreate ? '添加供应商' : '保存修改' }}
          </button>
        </div>
        <div v-if="confirmDelete" role="alert" class="text-sm flex items-center gap-2">
          <span>删除此供应商及其凭据？历史笔记会保留。</span><button class="btn-secondary" :disabled="saving" @click="confirmDelete = false">
            取消
          </button><button class="btn-primary" :disabled="saving" @click="remove">
            确认删除
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
    </p><button class="btn-secondary mt-3" @click="refresh">
      重新加载
    </button>
  </div>
</template>
