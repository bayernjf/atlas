import { afterEach, describe, expect, it, vi } from 'vitest'
import { DebugRunStoppedError, streamRun, type RunEvent } from '../apiClient'

function sseResponse(frames: Array<Record<string, unknown>>) {
  const body = frames
    .map((frame) => `event: ${frame.type ?? 'frame'}\ndata: ${JSON.stringify(frame)}\n\n`)
    .join('')
  return {
    ok: true,
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body))
        controller.close()
      },
    }),
  } as Response
}

const pausedFrame = {
  type: 'paused',
  token: 'dbg-1',
  node_id: 'trigger-1',
  node_type: 'trigger',
  reason: 'step',
  globals: { approval_limit: 500 },
  outputs: {},
}

const resultFrame = {
  id: 'graph-1',
  status: 'completed',
  outputs: { 'tool_call-1': { result: { status: 'SUCCESS' } } },
  trace: ['trigger-1'],
}

afterEach(() => vi.unstubAllGlobals())

describe('streamRun debug framing (04 §5.12)', () => {
  it('forwards paused frames without mistaking them for the result', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse([pausedFrame, resultFrame])))
    const events: RunEvent[] = []
    const result = await streamRun('graph-1', {}, (event) => events.push(event), {
      breakpoints: [],
    })
    expect(events).toHaveLength(1)
    expect(events[0].type).toBe('paused')
    expect(result.id).toBe('graph-1')

    const fetchMock = vi.mocked(fetch)
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(init.body as string)).toEqual({ inputs: {}, debug: { breakpoints: [] } })
  })

  it('rejects with DebugRunStoppedError after a stopped frame and no result', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse([pausedFrame, {
      type: 'stopped',
      node_id: 'tool_call-1',
      reason: 'user_stop',
    }])))
    const events: RunEvent[] = []
    await expect(streamRun('graph-1', {}, (event) => events.push(event))).rejects.toBeInstanceOf(
      DebugRunStoppedError,
    )
    expect(events.map((event) => event.type)).toEqual(['paused', 'stopped'])
  })
})
