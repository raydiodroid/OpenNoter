import axios from 'axios'
const request = axios.create({
  baseURL: '/api',
  timeout: 10000,
})
function handleErrorResponse(response: any) {
  if (!response) return '请求失败，请检查网络连接'
  if (typeof response.code !== 'number') return '系统异常'

  // 错误码判断
  switch (response.code) {
    case 1001:
      return response.msg || '下载失败，请检查视频链接'
    case 1002:
      return response.msg || '转写失败，请稍后重试'
    case 1003:
      return response.msg || '总结失败，可能是模型服务异常'
    case 2001:
    case 2002:
      return Array.isArray(response.data)
        ? response.data.map(e => `${e.field}: ${e.error}`).join('\n')
        : response.msg || '参数错误'
    default:
      return response.msg || '系统异常'
  }
}
export default request
