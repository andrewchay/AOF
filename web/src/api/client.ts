import axios, { type AxiosInstance } from 'axios'

// Axios 实例：dev 时走 vite proxy (/v1 -> localhost:8787)
const client: AxiosInstance = axios.create({
  baseURL: '/v1',
  timeout: 30000,
})

// 统一错误处理
client.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const message = error?.response?.data?.detail || error?.message || '请求失败'
    return Promise.reject(new Error(message))
  },
)

export default client
