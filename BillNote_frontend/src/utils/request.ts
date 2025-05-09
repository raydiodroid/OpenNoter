import axios from 'axios'

const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL + '/api' || 'http://localhost:8000/api',
  timeout: 10000,
})

export default request
