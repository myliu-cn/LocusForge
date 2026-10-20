import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, connect, formatBytes, formatTime, request } from './api'

describe('API contract and formatting', () => {
  beforeEach(() => { sessionStorage.clear() })
  afterEach(() => { vi.unstubAllGlobals() })

  it('attaches the session token without putting credentials in URLs', async () => {
    sessionStorage.setItem('locus.api-token', 'test-token-not-a-real-credential')
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    expect(await request('/system')).toEqual({ ok: true })
    const [url, options] = fetch.mock.calls[0]
    expect(url).toBe('/api/system')
    expect(new Headers(options.headers).get('Authorization')).toBe('Bearer test-token-not-a-real-credential')
    expect(options.credentials).toBe('same-origin')
  })

  it('reports server validation details instead of an empty success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: [{ msg: 'Width must be even' }, { msg: 'Scene is missing' }] }),
      { status: 422 },
    )))
    await expect(request('/plan', { method: 'POST', body: '{}' })).rejects.toThrow('Width must be even · Scene is missing')
  })

  it('restores the previous token after an unsuccessful connection', async () => {
    sessionStorage.setItem('locus.api-token', 'previous-test-token')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Unauthorized' }), { status: 401 })))
    await expect(connect('invalid-token')).rejects.toBeInstanceOf(ApiError)
    expect(sessionStorage.getItem('locus.api-token')).toBe('previous-test-token')
  })

  it('surfaces malformed proxy errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>502</html>', { status: 502 })))
    await expect(request('/jobs')).rejects.toThrow('服务未返回有效 JSON')
  })

  it('uses binary byte units and the actual timestamp rather than a rounded frame count', () => {
    expect(formatBytes(1024 ** 3)).toBe('1.0 GiB')
    expect(formatBytes(1.5 * 1024 ** 2)).toBe('1.5 MiB')
    expect(formatTime(719 / 24)).toBe('00:29.96')
    expect(formatTime(11.75)).toBe('00:11.75')
    expect(formatTime(60)).toBe('01:00.00')
  })
})
