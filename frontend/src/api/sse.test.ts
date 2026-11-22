import { afterEach, describe, expect, it, vi } from 'vitest';
import { frame, liveSse, sseResponse } from '../test/sse';
import { ApiError } from './http';
import { createSseParser, streamChat, type ChatStreamHandlers } from './sse';

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
});

function parse(chunks: string[]) {
  const events: [string, string][] = [];
  const parser = createSseParser((event, data) => events.push([event, data]));
  for (const chunk of chunks) parser.push(chunk);
  parser.end();
  return events;
}

describe('createSseParser', () => {
  it('reads an event with its name and data', () => {
    expect(parse(['event: delta\ndata: {"text":"hi"}\n\n'])).toEqual([['delta', '{"text":"hi"}']]);
  });

  it('reads several events from one chunk', () => {
    expect(parse(['event: a\ndata: 1\n\nevent: b\ndata: 2\n\n'])).toEqual([
      ['a', '1'],
      ['b', '2'],
    ]);
  });

  it('copes with chunks split in the middle of a line', () => {
    expect(parse(['eve', 'nt: del', 'ta\nda', 'ta: {"te', 'xt":"x"}\n', '\n'])).toEqual([
      ['delta', '{"text":"x"}'],
    ]);
  });

  it('copes with CRLF, including a chunk that splits the CR from the LF', () => {
    expect(parse(['event: a\r\ndata: 1\r\n\r\nevent: b\r', '\ndata: 2\r', '\n\r\n'])).toEqual([
      ['a', '1'],
      ['b', '2'],
    ]);
  });

  it('joins multiple data lines, ignores comments and defaults the name to "message"', () => {
    expect(parse([': keep-alive\ndata: one\ndata: two\n\n'])).toEqual([['message', 'one\ntwo']]);
  });

  it('handles a final event with no trailing blank line', () => {
    expect(parse(['event: done\ndata: {}'])).toEqual([['done', '{}']]);
  });

  it('treats a trailing bare CR as the end of the last line', () => {
    expect(parse(['event: done\rdata: {}\r'])).toEqual([['done', '{}']]);
  });

  it('does not emit an event that has no data', () => {
    expect(parse(['event: ping\n\n'])).toEqual([]);
  });
});

const message = (id: string, role: 'user' | 'assistant', content: string) => ({
  id,
  role,
  content,
  actions: [],
  author_is_me: role === 'user',
  author_user_id: null,
  created_at: '2026-01-01T00:00:00Z',
});

function recorder() {
  const log: string[] = [];
  const handlers: ChatStreamHandlers = {
    onAck: (m) => log.push(`ack:${m.content}`),
    onDelta: (t) => log.push(`delta:${t}`),
    onStatus: (l) => log.push(`status:${l}`),
    onAction: (a) => log.push(`action:${a.summary}`),
    onDone: (d) => log.push(`done:${d.message_id}`),
    onError: (e) => log.push(`error:${e.code}`),
  };
  return { log, handlers };
}

describe('streamChat', () => {
  it('posts the content with the CSRF header and routes each event', async () => {
    document.cookie = 'csrf_token=tok123';
    const fetchMock = vi.fn<typeof fetch>(async () =>
      sseResponse([
        frame('ack', { message: message('m1', 'user', 'hello') }),
        frame('status', { label: 'Looking' }),
        frame('delta', { text: 'Hi ' }),
        frame('delta', { text: 'there' }),
        frame('action', {
          id: 'a1',
          type: 'idea',
          summary: 'Pottery',
          payload: {},
          result: null,
          applied_at: null,
        }),
        frame('done', { message_id: 'm2', message: message('m2', 'assistant', 'Hi there') }),
      ]),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { log, handlers } = recorder();

    await streamChat('t1', 'hello', handlers, new AbortController().signal);

    expect(log).toEqual([
      'ack:hello',
      'status:Looking',
      'delta:Hi ',
      'delta:there',
      'action:Pottery',
      'done:m2',
    ]);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/api/v1/chat/threads/t1/messages');
    expect(init?.method).toBe('POST');
    expect(init?.credentials).toBe('same-origin');
    expect(init?.body).toBe(JSON.stringify({ content: 'hello' }));
    const headers = new Headers(init?.headers);
    expect(headers.get('X-CSRF-Token')).toBe('tok123');
    expect(headers.get('Content-Type')).toBe('application/json');
  });

  it('decodes a multibyte character whose bytes are split across chunks', async () => {
    const bytes = new TextEncoder().encode(frame('delta', { text: 'Dinner \u{1F377}!' }));
    const cut = bytes.indexOf(0xf0) + 2; // inside the four-byte emoji
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, cut));
        controller.enqueue(bytes.slice(cut));
        controller.close();
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(body, { status: 200 })),
    );
    const { log, handlers } = recorder();
    await streamChat('t1', 'x', handlers);
    expect(log).toEqual(['delta:Dinner \u{1F377}!']);
  });

  it('delivers an error event to onError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => sseResponse([frame('error', { code: 'ai_failed', message: 'Oops' })])),
    );
    const { log, handlers } = recorder();
    await streamChat('t1', 'x', handlers);
    expect(log).toEqual(['error:ai_failed']);
  });

  it('rejects with an ApiError when the request fails before streaming', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: { code: 'rate_limited', message: 'Slow down' } }), {
            status: 429,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    );
    const error = await streamChat('t1', 'x', {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, code: 'rate_limited', message: 'Slow down' });
  });

  it('resolves quietly when aborted mid-stream and stops delivering events', async () => {
    const live = liveSse();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => live.response),
    );
    const { log, handlers } = recorder();
    const controller = new AbortController();
    const done = streamChat('t1', 'x', handlers, controller.signal);
    live.emit('delta', { text: 'one' });
    await vi.waitFor(() => expect(log).toEqual(['delta:one']));
    controller.abort();
    await done;
    expect(log).toEqual(['delta:one']);
  });
});
