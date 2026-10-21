import { useEffect } from 'react'
import { isRunningStatus, useTaskStore } from '@/store/taskStore'
import { getTaskStatus } from '@/services/note'
import { toast } from '@/lib/toast'

/** 全局任务轮询：推进 PENDING → … → SUCCESS / FAILED，成功后落库多版本笔记 */
/** 服务端列表同步的间隔：比状态轮询稀疏得多（列表变化不频繁） */
const SYNC_INTERVAL = 30000

export const useTaskPolling = (interval = 3000) => {
  useEffect(() => {
    let busy = false

    // 启动时先拉一次服务端列表：换浏览器/设备后也能看到自己的历史
    void useTaskStore.getState().syncFromServer()
    const syncTimer = setInterval(() => {
      void useTaskStore.getState().syncFromServer()
    }, SYNC_INTERVAL)

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
    return () => {
      clearInterval(timer)
      clearInterval(syncTimer)
    }
  }, [interval])
}
