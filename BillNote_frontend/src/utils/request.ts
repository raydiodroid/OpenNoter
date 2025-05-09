import axios from 'axios'
const baseURL=import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const request = axios.create({
  baseURL: baseURL+'/api',
  timeout: 10000,
})

export default request
