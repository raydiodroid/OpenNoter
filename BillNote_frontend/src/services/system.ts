import request from '@/utils/request'

export const systemCheck=async()=>{
  return await request.get('/sys_health')
}
