<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { deleteMyCredential, getMyCredentials, saveMyCredential } from '~/logic/api'

const mode = ref('shared')
const asrMode = ref('shared')
const allowCustom = ref(true)
const allowedUrls = ref<string[]>([])
const message = ref('')
const busy = ref(false)
const capabilities = [{ id: 'default', label: '通用凭据（转写是否使用由站点策略决定）' }, { id: 'llm', label: '笔记与问答' }, { id: 'asr', label: '音频转写' }]
const forms = reactive<Record<string, { base_url: string, api_key: string, model_name: string, masked: string }>>({
  default: { base_url: '', api_key: '', model_name: '', masked: '' },
  llm: { base_url: '', api_key: '', model_name: '', masked: '' },
  asr: { base_url: '', api_key: '', model_name: '', masked: '' },
})
async function load() {
  try {
    const status = await getMyCredentials()
    allowCustom.value = status.allow_custom_base_url !== false
    allowedUrls.value = status.allowed_base_urls || []
    mode.value = status.mode
    asrMode.value = status.asr_mode ?? status.mode
    for (const cap of capabilities) {
      const own = status.credentials.find(item => item.capability === cap.id)
      forms[cap.id] = { base_url: own?.base_url ?? '', model_name: own?.model_name ?? '', api_key: '', masked: own?.masked_key ?? '' }
    }
  }
  catch (e) { message.value = (e as Error).message }
}
async function save(capability: string) {
  busy.value = true
  message.value = ''
  try {
    const form = forms[capability]
    if (!form.api_key.trim())
      throw new Error('请输入完整的新 API Key；插件不会回读已保存的密钥')
    await saveMyCredential(capability, { base_url: form.base_url, model_name: form.model_name, api_key: form.api_key })
    form.api_key = ''
    await load()
    message.value = '已保存到当前账号'
  }
  catch (e) { message.value = (e as Error).message }
  finally { busy.value = false }
}
async function remove(capability: string) {
  busy.value = true
  try {
    await deleteMyCredential(capability)
    await load()
    message.value = '已删除个人凭据'
  }
  catch (e) { message.value = (e as Error).message }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <div class="p-6 max-w-2xl">
    <h1 class="text-xl font-bold mb-4">
      我的模型凭据
    </h1>
    <p v-if="mode === 'shared' && asrMode === 'shared'" class="text-sm mb-4">
      站点当前使用共享模型。管理员开启个人凭据后，可在此配置自己的模型与音频转写服务。
    </p>
    <p v-if="message" class="text-sm mb-4" role="status">
      {{ message }}
    </p>
    <template v-if="mode !== 'shared' || asrMode !== 'shared'">
      <section v-for="cap in capabilities.filter(c => c.id === 'default' || (c.id === 'asr' ? asrMode : mode) !== 'shared')" :key="cap.id" class="section-card">
        <h2 class="font-semibold">
          {{ cap.label }}
        </h2>
        <p v-if="forms[cap.id].masked" class="text-xs text-gray-500">
          已配置：{{ forms[cap.id].masked }}
        </p>
        <label class="block my-2">接口地址
          <input v-if="allowCustom" v-model="forms[cap.id].base_url" class="input w-full" placeholder="https://provider.example/v1">
          <select v-else v-model="forms[cap.id].base_url" class="input w-full">
            <option value="">选择站点允许的接口地址</option>
            <option v-if="forms[cap.id].base_url && !allowedUrls.includes(forms[cap.id].base_url)" :value="forms[cap.id].base_url" disabled>当前地址不在允许列表中</option>
            <option v-for="url in allowedUrls" :key="url" :value="url">{{ url }}</option>
          </select>
        </label>
        <label class="block my-2">模型名称<input v-model="forms[cap.id].model_name" class="input w-full"></label>
        <label class="block my-2">API Key<input v-model="forms[cap.id].api_key" type="password" autocomplete="off" class="input w-full"></label>
        <div class="flex gap-2">
          <button class="btn-primary" :disabled="busy" @click="save(cap.id)">
            保存
          </button>
          <button class="btn-secondary" :disabled="busy || !forms[cap.id].masked" @click="remove(cap.id)">
            删除个人凭据
          </button>
        </div>
      </section>
    </template>
  </div>
</template>
