import { useEffect } from 'react'
import { isRunningStatus, useTaskStore } from '@/store/taskStore'
import { getTaskStatus } from '@/services/note'
import { toast } from '@/lib/toast'

/** 全局任务轮询：推进 PENDING → … → SUCCESS / FAILED，成功后落库多版本笔记 */
export const useTaskPolling = (interval = 3000) => {
  useEffect(() => {
    let busy = false

    const tick = async () => {
      if (busy) return
      const { tasks, updateTask, markSuccess } = useTaskStore.getState()
      const active = tasks.filter(task => isRunningStatus(task.status))
      if (active.length === 0) return

      busy = true
      try {
        for (const task of active) {
          try {
            const res = await getTaskStatus(task.id)
            if (res.status === 'SUCCESS' && res.result) {
              markSuccess(task.id, {
                markdown: res.result.markdown ?? '',
                transcript: res.result.transcript,
                audioMeta: res.result.audio_meta,
              })
              toast.success('笔记生成成功')
            } else if (res.status === 'FAILED') {
              updateTask(task.id, { status: 'FAILED' })
              toast.error(res.message || '笔记生成失败，请检查后台日志')
            } else if (res.status && res.status !== task.status) {
              updateTask(task.id, { status: res.status })
            }
          } catch {
            // 单次轮询失败不把任务判死，等下一轮
          }
        }
      } finally {
        busy = false
      }
    }

    const timer = setInterval(tick, interval)
    tick()
    return () => clearInterval(timer)
  }, [interval])
}
