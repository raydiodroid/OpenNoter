import { ApiError } from './client'
import type { TaskStatusResponse } from './types'

/** One polling chain per surface; stale responses cannot restart a stopped chain. */
export function createTaskPoller(
  read: (id: string) => Promise<TaskStatusResponse>,
  result: (id: string, data: TaskStatusResponse) => void,
  error: (error: Error) => void,
) {
  let generation = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  function stop() {
    generation++
    clearTimeout(timer)
  }
  function start(id: string) {
    stop()
    const current = generation
    const poll = async () => {
      let delay = 3000
      try {
        const data = await read(id)
        if (current !== generation)
          return
        result(id, data)
        if (data.status === 'SUCCESS' || data.status === 'FAILED')
          return
      }
      catch (e) {
        if (current !== generation)
          return
        error(e as Error)
        if (e instanceof ApiError && [401, 403, 404, 409, 410].includes(e.status))
          return
        delay = 5000
      }
      if (current === generation)
        timer = setTimeout(poll, delay)
    }
    void poll()
  }
  return { start, stop }
}
