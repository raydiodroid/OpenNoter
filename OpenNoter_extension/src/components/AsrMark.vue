<script setup lang="ts">
import { computed } from 'vue'
import Cloud from '~icons/lucide/cloud'
import Server from '~icons/lucide/server'
import Audio from '~icons/lucide/audio-lines'
import Computer from '~icons/lucide/cpu'
import Apple from '~icons/simple-icons/apple'
import bcutLogo from '~/assets/brands/bcut-official.png'
import Kuaishou from '~icons/simple-icons/kuaishou'
import OpenAI from '~icons/simple-icons/openai'
// Vendored unmodified Boxicons Groq logo (Iconify JSON 2.2.546, bxl/groq-ai).
import groqLogo from '~/assets/brands/groq.svg'

const props = defineProps<{ name: string }>()
const icons = { cloud: Cloud, server: Server, audio: Audio, whisper: Computer, apple: Apple, kuaishou: Kuaishou, openai: OpenAI }
const component = computed(() => icons[props.name as keyof typeof icons] || Cloud)
</script>

<template>
  <span aria-hidden="true" class="inline-flex items-center justify-center shrink-0 rounded border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100 w-8 h-8">
    <!-- Official unmodified white logo from bcut.bilibili.cn, app.f3896fe6.js module 178a. -->
    <span v-if="name === 'bcut' || name === 'groq'" class="w-5 h-5" :style="{ backgroundColor: 'currentColor', mask: `url(${name === 'bcut' ? bcutLogo : groqLogo}) center / contain no-repeat` }" />
    <component v-else :is="component" class="w-5 h-5" />
  </span>
</template>
