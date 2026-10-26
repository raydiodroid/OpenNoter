<script setup lang="ts">
import { computed } from 'vue'
import type { TaskStatus } from '~/logic/types'

const props = defineProps<{ status: TaskStatus, message?: string }>()

// 进度条只展示**后端真的会写入**的状态。
//
// `FORMATTING` 在后端枚举/配额/中断集合里都存在，但 `NoteGenerator` 从来不写
// 它（`_update_status` 只发 PENDING/PARSING/DOWNLOADING/TRANSCRIBING/
// SUMMARIZING/SAVING/SUCCESS/FAILED）。放进 STAGE_ORDER 会让进度条在
// SUMMARIZING → SAVING 之间**空跳一格**，看起来像「卡住了」。
//
// 注意：`STAGE_LABELS` 仍保留 FORMATTING（它是 `Record<TaskStatus, string>`，
// 少一项 TS 会报错），只是不再参与进度计算。若将来后端真的开始写该状态，
// 把它加回本数组即可。
const STAGE_ORDER: TaskStatus[] = ['PENDING', 'PARSING', 'DOWNLOADING', 'TRANSCRIBING', 'SUMMARIZING', 'SAVING', 'SUCCESS']
const STAGE_LABELS: Record<TaskStatus, string> = {
  PENDING: '排队中',
  PARSING: '解析中',
  DOWNLOADING: '下载中',
  TRANSCRIBING: '转写中',
  SUMMARIZING: '总结中',
  FORMATTING: '格式化',
  SAVING: '保存中',
  SUCCESS: '完成',
  FAILED: '失败',
}

const currentIdx = computed(() => STAGE_ORDER.indexOf(props.status))
const isFailed = computed(() => props.status === 'FAILED')
</script>

<template>
  <div class="flex flex-col gap-2">
    <div class="flex items-center gap-2 text-sm">
      <span :class="isFailed ? 'text-red-600' : 'text-blue-600'" class="font-medium">
        {{ STAGE_LABELS[status] }}
      </span>
      <span v-if="message" class="text-gray-500 text-xs truncate">{{ message }}</span>
    </div>
    <div v-if="!isFailed" class="flex gap-1">
      <div
        v-for="(s, i) in STAGE_ORDER"
        :key="s"
        class="h-1 flex-1 rounded-full"
        :class="i <= currentIdx ? 'bg-blue-500' : 'bg-gray-200'"
      />
    </div>
    <div v-else class="h-1 rounded-full bg-red-500" />
  </div>
</template>
