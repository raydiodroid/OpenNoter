import { useEffect, useState } from 'react'
import request from '@/utils/request'

const MAX_RETRIES = 3
const RETRY_INTERVAL = 10000 // 10秒

export const useCheckBackend = () => {
  const [loading, setLoading] = useState(false)
  const [initialized, setInitialized] = useState(false)

  useEffect(() => {
    let retries = 0

    const check = async () => {
      try {
        await request.get('/sys_health')
        setInitialized(true)
        setLoading(false)
      } catch {
        if (retries === 0) {
          // 第一次失败时开始显示加载状态
          setLoading(true)
        }

        if (retries < MAX_RETRIES) {
          retries++
          setTimeout(check, RETRY_INTERVAL)
        } else {
          // 达到重试上限，继续轮询直到后端就绪
          waitUntilBackendReady()
        }
      }
    }

    const waitUntilBackendReady = async () => {
      while (true) {
        try {
          await request.get('/sys_health')
          setInitialized(true)
          setLoading(false)
          break
        } catch {
          await new Promise(res => setTimeout(res, RETRY_INTERVAL))
        }
      }
    }

    check()
  }, [])

  return { loading, initialized }
}