export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message)
    this.name = 'ApiError'
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function responseMessage(value: unknown, status: number): string {
  if (typeof value === 'object' && value !== null && 'detail' in value) {
    const detail = value.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail.map((item: unknown) => {
        if (typeof item === 'object' && item !== null && 'msg' in item) return String(item.msg)
        return JSON.stringify(item)
      }).join(' · ')
    }
  }
  return `请求失败（HTTP ${status}）`
}

export function storedToken(): string {
  return sessionStorage.getItem('locus.api-token') ?? ''
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers)
  const token = storedToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let detail: unknown
    try {
      detail = await response.json()
    } catch {
      throw new ApiError(`服务未返回有效 JSON（HTTP ${response.status}）`, response.status)
    }
    throw new ApiError(responseMessage(detail, response.status), response.status)
  }
  return response.json() as Promise<T>
}

export async function connect(token: string): Promise<void> {
  const previous = storedToken()
  sessionStorage.setItem('locus.api-token', token.trim())
  try {
    await request('/session', { method: 'POST' })
  } catch (error) {
    sessionStorage.setItem('locus.api-token', previous)
    throw error
  }
}

export function uploadScene<T>(
  file: File, metadata: Record<string, unknown>, progress: (percent: number) => void,
): { result: Promise<T>; abort: () => void } {
  const xhr = new XMLHttpRequest()
  const result = new Promise<T>((resolve, reject) => {
    xhr.open('POST', '/api/scenes')
    xhr.withCredentials = true
    if (storedToken()) xhr.setRequestHeader('Authorization', `Bearer ${storedToken()}`)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100))
    }
    xhr.onerror = () => reject(new ApiError('上传连接中断；请检查服务后重试。', 0))
    xhr.onabort = () => reject(new ApiError('上传已取消。', 0))
    xhr.onload = () => {
      let body: unknown
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        reject(new ApiError(`上传服务响应无效（HTTP ${xhr.status}）`, xhr.status))
        return
      }
      if (xhr.status < 200 || xhr.status >= 300) reject(new ApiError(responseMessage(body, xhr.status), xhr.status))
      else resolve(body as T)
    }
    const form = new FormData()
    form.append('file', file)
    form.append('metadata', JSON.stringify(metadata))
    xhr.send(form)
  })
  return { result, abort: () => xhr.abort() }
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GiB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`
  return `${(bytes / 1024).toFixed(1)} KiB`
}

export function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes).padStart(2, '0')}:${(seconds % 60).toFixed(2).padStart(5, '0')}`
}

export function saveJson(value: unknown, name: string): void {
  const objectUrl = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = name
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
}
