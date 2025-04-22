import request from '@/utils/request.ts'

export const getProviderList = async () => {
  return await request.get('/get_all_providers')
}
