import { useEffect } from 'react'
import { isRunningStatus, useTaskStore } from '@/store/taskStore'
import { getTaskStatus } from '@/services/note'
import { toast } from '@/lib/toast'
import type { Task } from '@/types'

/** 全局任务轮询：推进 PENDING → … → SUCCESS / FAILED，成功后落库多版本笔记 */
/** 服务端列表同步的间隔：比状态轮询稀疏得多（列表变化不频繁） */
const SYNC_INTERVAL = 30000

/** 连续鉴权失败次数达到阈值后判 FAILED，避免 403 被空吞导致永远「生成中」 */
const AUTH_FAIL_THRESHOLD = 3

/**
 * 占位条目补拉正文的重试上限。
 *
 * `syncFromServer` 为远端任务建的空正文条目（`markdown: []`，状态 SUCCESS）需要主动拉一次
 * `/tasks/{id}` 才有内容；但「结果文件确实不存在」时无限重拉就是每 3 秒一次的无效请求。
 * 3 次足够覆盖「后端落盘慢一拍」（F4 的 SUCCESS 早于落盘窗口），之后不再打接口。
 */
const HYDRATE_MAX_ATTEMPTS = 3

/**
 * 是否需要补拉正文：占位条目 = 状态 SUCCESS 但本地没有任何 markdown 版本。
 *
 * 只看「本地有没有正文」，**不看 `has_result`** —— 该字段在 `list_tasks_for_user` 的
 * DB 分支不存在，依赖它会让判断恒假（见方案 F10 的说明）。
 */
const taskNeedsHydration = (task: Task) => task.status === 'SUCCESS' && task.markdown.length === 0

export const useTaskPolling = (interval = 3000) => {
  useEffect(() => {
    let busy = false
    const authFailCounts = new Map<string, number>()
    // taskId → 已尝试补拉次数。用 Map 而非 Set，保证「后端落盘慢一拍」时还能重试几次。
    // 只在本 hook 生命周期内有效：刷新页面重新计数（最多再多 3 次），不会无限增长。
    const hydrateAttempts = new Map<string, number>()

    // 启动时先拉一次服务端列表：换浏览器/设备后也能看到自己的历史
    void useTaskStore.getState().syncFromServer()
    const syncTimer = setInterval(() => {
      void useTaskStore.getState().syncFromServer()
    }, SYNC_INTERVAL)

    const tick = async () => {
      if (busy) return
      const { tasks, updateTask, markSuccess, hydrateTask } = useTaskStore.getState()
      const active = tasks.filter(task => isRunningStatus(task.status))
      // 占位条目：syncFromServer 建的空正文条目（远端 SUCCESS、本地 markdown 为空）。
      // 它们被 isRunningStatus 排除在 active 之外，不补拉就永远空白（F10）。
      const placeholders = tasks.filter(taskNeedsHydration)
      if (active.length === 0 && placeholders.length === 0) return

      busy = true
      try {
        for (const task of placeholders) {
          const tried = hydrateAttempts.get(task.id) ?? 0
          // 超过上限：认定服务端结果确实不存在，别再打接口
          if (tried >= HYDRATE_MAX_ATTEMPTS) continue
          hydrateAttempts.set(task.id, tried + 1)
          if (await hydrateTask(task.id)) hydrateAttempts.delete(task.id)
        }

        for (const task of active) {
          try {
            const res = await getTaskStatus(task.id)
            authFailCounts.delete(task.id)
            if (res.status === 'SUCCESS' && res.result) {
              markSuccess(task.id, {
                markdown: res.result.markdown ?? '',
                transcript: res.result.transcript,
                audioMeta: res.result.audio_meta,
              })
              toast.success('笔记生成成功')
            } else if (res.status === 'FAILED') {
              // 把后端 message 一起存进 store：失败页要显示**真实原因**
              // （如「B站下载失败: HTTP Error 412」），而不是一句泛泛的提示。
              updateTask(task.id, { status: 'FAILED', message: res.message })
              toast.error(res.message || '笔记生成失败')
            } else if (res.status && res.status !== task.status) {
              // 非终态也记 message：轮询期间用户能看到「下载中 / 转写中」等进度描述
              updateTask(task.id, { status: res.status, message: res.message })
            }
          } catch (err: unknown) {
            const code = (err as { code?: number })?.code
            // 401/403：鉴权或归属失败，再吞下去只会永远「生成中」
            if (code === 401 || code === 403) {
              const n = (authFailCounts.get(task.id) || 0) + 1
              authFailCounts.set(task.id, n)
              if (n >= AUTH_FAIL_THRESHOLD) {
                const reason =
                  code === 401
                    ? '未登录或登录已过期，无法查询任务进度'
                    : '无权访问该任务，已停止轮询'
                updateTask(task.id, { status: 'FAILED', message: reason })
                toast.error(reason)
                authFailCounts.delete(task.id)
              }
              continue
            }
            // 其它单次轮询失败不把任务判死，等下一轮
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
