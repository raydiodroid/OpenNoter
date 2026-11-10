<script setup lang="ts">
import { computed } from 'vue'
import Cloud from '~icons/lucide/cloud'
import Server from '~icons/lucide/server'
import Audio from '~icons/lucide/audio-lines'
import Computer from '~icons/lucide/cpu'
import Apple from '~icons/simple-icons/apple'
import Cut from '~icons/lucide/scissors'
import Video from '~icons/lucide/video'
import OpenAI from '~icons/simple-icons/openai'
import Groq from '~icons/lucide/zap'
import { ASR_ICONS } from '~/logic/asr-icons'

const props = defineProps<{ name: string }>()
const icons = { cloud: Cloud, server: Server, audio: Audio, whisper: Computer, apple: Apple, bcut: Cut, kuaishou: Video, openai: OpenAI, groq: Groq }
const icon = computed(() => ASR_ICONS.find(icon => icon.id === props.name) || ASR_ICONS[0])
const component = computed(() => icons[props.name as keyof typeof icons] || Cloud)
</script>

<template>
  <span aria-hidden="true" class="inline-flex items-center justify-center shrink-0 rounded border w-8 h-8" :style="{ color: icon.color, background: `${icon.color}12`, borderColor: `${icon.color}30` }">
    <component :is="component" class="w-5 h-5" />
  </span>
</template>
