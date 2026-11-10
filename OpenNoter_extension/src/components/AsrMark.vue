<script setup lang="ts">
import { computed } from 'vue'
import { ASR_ICONS } from '~/logic/asr-icons'

const props = defineProps<{ name: string }>()
const icon = computed(() => ASR_ICONS.find(icon => icon.id === props.name) || ASR_ICONS[0])
</script>

<template>
  <span aria-hidden="true" class="inline-flex items-center justify-center shrink-0 rounded border w-8 h-8" :style="{ color: icon.color, background: `${icon.color}12`, borderColor: `${icon.color}30` }">
    <svg v-if="icon.path" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path :d="icon.path" /></svg>
    <span v-else class="font-bold text-base">{{ name === 'groq' ? 'G' : 'O' }}</span>
  </span>
</template>
