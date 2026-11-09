<script setup lang="ts">
import 'uno.css'
import { computed, ref } from 'vue'
import { sendMessage } from 'webext-bridge/content-script'
import { PLATFORM_LABELS, detectPlatform } from '~/logic/platform'

const platform = detectPlatform(window.location.href)
const busy = ref(false)
const toast = ref<{ kind: 'ok' | 'err', text: string } | null>(null)

const label = computed(() => platform ? `用 OpenNoter 总结这个${PLATFORM_LABELS[platform]}视频` : '')
const cookieBusy = ref(false)
const cookieToast = ref<string | null>(null)

/**
 * 取出当前站点的 Cookie 并复制到剪贴板。
 *
 * 为什么不走后端：自建部署的域名不在扩展的 content script 注入白名单里
 * （`OPENNOTER_HOST_PERMISSIONS` 只含 localhost），网页端那条「一键同步」
 * 在非 localhost 上根本不启动。这条路径直接站在视频站点页面上取，
 * 让用户自己粘到设置页 —— 不依赖后端、不依赖额外域名权限。
 */
async function copyCookie() {
  if (!platform || cookieBusy.value)
    return
  cookieBusy.value = true
  cookieToast.value = null
  try {
    const res = await sendMessage('opennoter-read-cookie', { platform }, 'background') as
      | { ok: true, cookie: string, count: number }
      | { ok: false, error: string }
    if (!res || !res.ok) {
      cookieToast.value = `❌ ${(res as any)?.error || '读取失败'}`
      return
    }
    await navigator.clipboard.writeText((res as any).cookie)
    cookieToast.value = `✅ 已复制 ${(res as any).count} 条 Cookie，去设置页粘贴`
  }
  catch (e) {
    cookieToast.value = `❌ ${(e as Error).message || '复制失败'}`
  }
  finally {
    cookieBusy.value = false
    setTimeout(() => {
      cookieToast.value = null
    }, 5000)
  }
}

async function trigger() {
  if (!platform || busy.value)
    return
  busy.value = true
  toast.value = null
  try {
    const res = await sendMessage('opennoter-start', {
      url: window.location.href,
      platform,
      title: document.title,
    }, 'background')
    const ok = res && (res as any).ok
    toast.value = ok
      ? { kind: 'ok', text: '已开始生成笔记，可在侧边栏 / popup 查看进度' }
      : { kind: 'err', text: (res as any)?.error || '提交失败，请打开设置检查后端与供应商' }
  }
  catch (e) {
    toast.value = { kind: 'err', text: (e as Error).message }
  }
  finally {
    busy.value = false
    setTimeout(() => { toast.value = null }, 4000)
  }
}
</script>

<template>
  <div v-if="platform" class="opennoter-fab fixed bottom-24 right-6 z-[2147483647] flex flex-col items-end gap-2 font-sans select-none">
    <div
      v-if="toast"
      class="text-xs px-3 py-2 rounded shadow max-w-[260px]"
      :class="toast.kind === 'ok' ? 'bg-green-600 text-white' : 'bg-red-600 text-white'"
    >
      {{ toast.text }}
    </div>
    <div
      v-if="cookieToast"
      class="text-xs px-3 py-2 rounded shadow max-w-[260px] bg-neutral-800 text-white"
    >
      {{ cookieToast }}
    </div>
    <button
      class="flex items-center gap-2 px-3 py-2 rounded-full shadow-lg cursor-pointer border-none text-white text-sm font-medium bg-pink-600 hover:bg-pink-700 disabled:bg-pink-300"
      :disabled="busy"
      :title="label"
      @click="trigger"
    >
      <span class="text-base">📝</span>
      <span>{{ busy ? '提交中…' : 'OpenNoter' }}</span>
    </button>
    <button
      class="flex items-center gap-2 px-3 py-2 rounded-full shadow-lg cursor-pointer border-none text-white text-sm font-medium bg-neutral-700 hover:bg-neutral-800 disabled:bg-neutral-400"
      :disabled="cookieBusy"
      title="复制当前站点的 Cookie 到剪贴板，粘贴到 OpenNoter 设置页"
      @click="copyCookie"
    >
      <span class="text-base">🍪</span>
      <span>{{ cookieBusy ? '读取中…' : '复制 Cookie' }}</span>
    </button>
  </div>
</template>
