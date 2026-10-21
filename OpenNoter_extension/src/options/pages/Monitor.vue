<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getDeployStatus, getSysHealth, type SysHealth } from '~/logic/api'
import type { DeployStatus } from '~/logic/types'

const status = ref<DeployStatus | null>(null)
const health = ref<SysHealth | null>(null)
const loading = ref(false)
const error = ref('')
/** 本页这次请求的真实耗时——可证伪指标，取代后端硬编码的 "running" */
const latencyMs = ref<number | null>(null)

const isLocalEngine = computed(() =>
  ['fast-whisper', 'mlx-whisper'].includes(status.value?.whisper.transcriber_type || ''),
)

async function refresh() {
  loading.value = true
  error.value = ''
  const startedAt = performance.now()
  try {
    const [s, h] = await Promise.all([getDeployStatus(), getSysHealth()])
    status.value = s
    health.value = h
    latencyMs.value = Math.round(performance.now() - startedAt)
  }
  catch (e) {
    error.value = (e as Error).message
  }
  finally {
    loading.value = false
  }
}

/** 'ok' | 'missing' | 'error' | undefined → 样式类 */
function healthTagClass(state?: string): string {
  if (state === 'ok')
    return 'bg-green-100 text-green-700'
  if (state === 'missing' || state === 'error')
    return 'bg-red-100 text-red-700'
  return 'bg-gray-100 text-gray-600' // undefined = 未探测
}

function healthLabel(state?: string): string {
  if (state === 'ok')
    return '正常'
  if (state === 'missing')
    return '缺失'
  if (state === 'error')
    return '异常'
  return '未知'
}

onMounted(refresh)
</script>

<template>
  <div class="p-6 max-w-2xl">
    <div class="flex items-center justify-between mb-4">
      <h1 class="text-xl font-bold">部署监控</h1>
      <button class="btn-secondary" :disabled="loading" @click="refresh">
        {{ loading ? '检查中…' : '刷新' }}
      </button>
    </div>

    <div v-if="error" class="text-red-600 text-sm mb-4">{{ error }}</div>

    <template v-if="status">
      <section class="section-card">
        <h2 class="font-semibold">后端 / 依赖</h2>
        <div class="text-sm space-y-1">
          <!-- 不再展示后端硬编码的 "running"：那个绿 tag 证伪能力为 0
               （要么请求失败整块不渲染，要么必然显示 running）。
               改成「本页请求耗时」——它是真实测量、且能变差的可证伪指标。 -->
          <div>
            响应
            <span class="tag bg-green-100 text-green-700">{{ latencyMs != null ? `${latencyMs} ms` : '—' }}</span>
            <span class="ml-2 text-gray-600">端口 {{ status.backend.port }}</span>
          </div>
          <div>
            FFmpeg
            <span class="tag" :class="healthTagClass(health?.ffmpeg)">
              {{ healthLabel(health?.ffmpeg) }}
            </span>
          </div>
          <div>
            数据库
            <span class="tag" :class="healthTagClass(health?.db)">
              {{ healthLabel(health?.db) }}
            </span>
          </div>
          <div v-if="health && !health.ok" class="text-red-600 text-xs">{{ health.msg }}</div>
        </div>
      </section>

      <section class="section-card">
        <h2 class="font-semibold">FFmpeg（deploy_status）</h2>
        <div class="text-sm flex items-center gap-3">
          <span
            class="tag"
            :class="status.ffmpeg.available ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'"
          >{{ status.ffmpeg.available ? '可用' : '不可用' }}</span>
        </div>
      </section>

      <section class="section-card">
        <h2 class="font-semibold">CUDA / GPU</h2>
        <div class="text-sm">
          <span
            class="tag"
            :class="status.cuda.available ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'"
          >{{ status.cuda.available ? '可用' : '不可用' }}</span>
          <div v-if="status.cuda.available" class="mt-1 text-gray-600 text-xs">
            CUDA {{ status.cuda.version }} · {{ status.cuda.gpu_name }}
          </div>
        </div>
      </section>

      <section class="section-card">
        <h2 class="font-semibold">Whisper</h2>
        <div class="text-sm text-gray-600">
          引擎：<span class="text-gray-800">{{ status.whisper.transcriber_type }}</span>
          <span v-if="status.whisper.model_size" class="ml-3">
            模型：<span class="text-gray-800">{{ status.whisper.model_size }}</span>
          </span>
          <!-- 本地引擎才谈得上"是否已下载"：在线引擎这个字段本来就是 false -->
          <span v-if="isLocalEngine" class="ml-3">
            状态：<span class="tag" :class="health?.whisper?.downloaded ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'">
              {{ health?.whisper?.downloaded ? '已下载' : '未下载' }}
            </span>
          </span>
        </div>
      </section>
    </template>
  </div>
</template>
