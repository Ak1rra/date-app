import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Schemas } from '../api/client';
import { husbandProfile, session, wifeProfile } from '../test/fixtures';
import { mockApi, type Handler } from '../test/mockApi';
import { jsonResponse, renderApp, renderAppWithClient } from '../test/renderApp';
import { frame, liveSse, sseResponse } from '../test/sse';

afterEach(() => {
  vi.unstubAllGlobals();
});

type Message = Schemas['ChatMessageOut'];
type Action = Schemas['ChatActionOut'];

const mineThread: Schemas['ChatThreadOut'] = {
  id: 't-mine',
  scope: 'private',
  title: null,
  updated_at: '2026-01-01T00:00:00Z',
};
const sharedThread: Schemas['ChatThreadOut'] = {
  ...mineThread,
  id: 't-shared',
  scope: 'shared',
};

function message(overrides: Partial<Message> = {}): Message {
  return {
    id: 'm1',
    role: 'user',
    content: 'Hello',
    actions: [],
    author_is_me: true,
    author_user_id: 'u1',
    created_at: '2026-01-01T10:00:00Z',
    ...overrides,
  };
}

function action(overrides: Partial<Action> = {}): Action {
  return {
    id: 'a1',
    type: 'idea',
    summary: 'Try a pottery class',
    payload: { title: 'Pottery night' },
    result: null,
    applied_at: null,
    ...overrides,
  };
}

const alex = { ...wifeProfile, subject_joined: true };

function chatApi(extra: Record<string, Handler> = {}, threads = [mineThread]) {
  return mockApi({
    'GET /api/v1/auth/session': session('Sam'),
    'GET /api/v1/meta': { demo_mode: false, version: 'test', no_login: true },
    'GET /api/v1/profiles': [husbandProfile, alex],
    'GET /api/v1/chat/threads': threads,
    'GET /api/v1/chat/threads/t-mine/messages': { messages: [], has_more: false },
    ...extra,
  });
}

const composer = () => screen.findByRole('textbox', { name: 'Message' });

/** A messages route backed by a list the test can add to, like the server's store. */
function store(initial: Message[] = []) {
  const messages = [...initial];
  const handler = () => ({ messages, has_more: false });
  return { messages, handler };
}

async function sendHello(user: ReturnType<typeof userEvent.setup>, text = 'Plan something') {
  await user.type(await composer(), text);
  // Send waits for the conversation's history to load.
  await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: 'Send' }));
}

describe('ChatPage empty state and threads', () => {
  it('shows a friendly intro whose chips fill the composer', async () => {
    const user = userEvent.setup();
    chatApi();
    renderApp('/chat');

    expect(await screen.findByRole('heading', { name: 'Chat' })).toBeInTheDocument();
    expect(await screen.findByText("Let's plan something")).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Plan a cozy night in' }));
    expect(await composer()).toHaveValue('Plan a cozy night in');
    expect(screen.getByRole('button', { name: 'Find something fun this weekend' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Surprise idea under $50' })).toBeVisible();
  });

  it('hides Together without a shared thread and hints at inviting a partner', async () => {
    chatApi();
    renderApp('/chat');

    expect(await screen.findByText(/invite your partner/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Together' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mine' })).not.toBeInTheDocument();
  });

  it('switches to the shared thread, naming and colouring each author', async () => {
    const user = userEvent.setup();
    chatApi(
      {
        'GET /api/v1/chat/threads/t-shared/messages': {
          messages: [
            message({ id: 's1', content: 'Dinner Friday?', author_is_me: true }),
            message({
              id: 's2',
              content: 'Yes please',
              author_is_me: false,
              author_user_id: 'u2',
            }),
            message({ id: 's3', role: 'assistant', content: 'Booking ideas coming', actions: [] }),
          ],
          has_more: false,
        },
      },
      [mineThread, sharedThread],
    );
    renderApp('/chat');

    expect(await screen.findByRole('button', { name: 'Mine' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await user.click(screen.getByRole('button', { name: 'Together' }));

    const mine = await screen.findByText('Dinner Friday?');
    const theirs = screen.getByText('Yes please');
    expect(screen.getByText('Sam')).toBeInTheDocument();
    expect(screen.getByText('Alex')).toBeInTheDocument();
    // Sam is the owner (husband, blue); Alex is the partner (wife, warm).
    expect(mine).toHaveClass('bg-husband-soft');
    expect(theirs).toHaveClass('bg-wife-soft');
    expect(screen.getByText('Booking ideas coming')).toHaveClass('bg-surface-2');
  });
});

describe('ChatPage sending', () => {
  it('streams a reply: pending bubble, ack, status, deltas, action card, then done', async () => {
    const user = userEvent.setup();
    const server = store();
    const live = liveSse();
    const api = chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': server.handler,
      'POST /api/v1/chat/threads/t-mine/messages': live.response,
    });
    renderApp('/chat');

    await sendHello(user);
    expect(api.body('POST', '/api/v1/chat/threads/t-mine/messages')).toEqual({
      content: 'Plan something',
    });
    // Optimistic: shown before the server has acknowledged it.
    expect(await screen.findByText('Plan something')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    expect(await composer()).toHaveValue('');

    const ackMessage = message({ id: 'm-user', content: 'Plan something' });
    server.messages.push(ackMessage);
    await act(async () => live.emit('ack', { message: ackMessage }));
    // Replaced by the stored message: still one bubble with that text.
    expect(screen.getAllByText('Plan something')).toHaveLength(1);
    expect(screen.queryByText(/\(sending\)/)).not.toBeInTheDocument();

    await act(async () => live.emit('status', { label: 'Checking the calendar' }));
    expect(screen.getByText('Checking the calendar')).toBeInTheDocument();

    await act(async () => live.emit('delta', { text: 'How about ' }));
    expect(screen.queryByText('Checking the calendar')).not.toBeInTheDocument();
    await act(async () => live.emit('delta', { text: 'pottery?' }));
    const streaming = screen.getByTestId('streaming-reply');
    expect(within(streaming).getByText('How about pottery?')).toBeInTheDocument();
    expect(screen.getByRole('log', { name: 'Conversation' })).toHaveAttribute('aria-busy', 'true');

    await act(async () => live.emit('action', action()));
    expect(within(streaming).getByText('Try a pottery class')).toBeInTheDocument();
    expect(within(streaming).getByText('Pottery night')).toBeInTheDocument();

    const reply = message({
      id: 'm-reply',
      role: 'assistant',
      content: 'How about pottery?',
      author_is_me: false,
      author_user_id: null,
      actions: [action()],
    });
    server.messages.push(reply);
    await act(async () => {
      live.emit('done', { message_id: 'm-reply', message: reply });
      live.close();
    });

    await vi.waitFor(() => expect(screen.queryByTestId('streaming-reply')).not.toBeInTheDocument());
    expect(screen.getAllByText('How about pottery?')).toHaveLength(1);
    expect(screen.getAllByText('Try a pottery class')).toHaveLength(1);
    expect(screen.getByRole('log', { name: 'Conversation' })).toHaveAttribute('aria-busy', 'false');
    expect(screen.getByText('The assistant replied.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled(); // empty draft again
  });

  it('sends on Enter and keeps Shift+Enter for a new line', async () => {
    const user = userEvent.setup();
    const live = liveSse();
    const api = chatApi({ 'POST /api/v1/chat/threads/t-mine/messages': live.response });
    renderApp('/chat');

    const box = await composer();
    await user.type(box, 'line one{Shift>}{Enter}{/Shift}line two');
    expect(box).toHaveValue('line one\nline two');
    expect(api.find('POST', '/api/v1/chat/threads/t-mine/messages')).toHaveLength(0);
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
    await user.keyboard('{Enter}');
    expect(api.body('POST', '/api/v1/chat/threads/t-mine/messages')).toEqual({
      content: 'line one\nline two',
    });
  });

  it('shows a counter near the 2000 character limit', async () => {
    const user = userEvent.setup();
    chatApi();
    renderApp('/chat');

    const box = await composer();
    expect(screen.queryByText(/\/ 2000/)).not.toBeInTheDocument();
    await user.click(box);
    await user.paste('a'.repeat(1850));
    expect(screen.getByText('1850 / 2000')).toBeInTheDocument();
  });

  it('shows an error event under the composer, removes the streaming bubble, keeps my message', async () => {
    const user = userEvent.setup();
    const server = store();
    const live = liveSse();
    chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': server.handler,
      'POST /api/v1/chat/threads/t-mine/messages': live.response,
    });
    renderApp('/chat');

    await sendHello(user);
    const ackMessage = message({ id: 'm-user', content: 'Plan something' });
    server.messages.push(ackMessage);
    await act(async () => live.emit('ack', { message: ackMessage }));
    await act(async () => live.emit('delta', { text: 'Partial' }));
    await act(async () => {
      live.emit('error', { code: 'ai_unavailable', message: 'The planner is resting.' });
      live.close();
    });

    expect(await screen.findByRole('alert')).toHaveTextContent('The planner is resting.');
    expect(screen.queryByTestId('streaming-reply')).not.toBeInTheDocument();
    expect(screen.queryByText('Partial')).not.toBeInTheDocument();
    expect(screen.getByText('Plan something', { selector: 'div' })).toBeInTheDocument();
    // The message was stored (acked), so it is not put back in the composer.
    expect(await composer()).toHaveValue('');
  });

  it('explains a 429 before the stream starts', async () => {
    const user = userEvent.setup();
    chatApi({
      'POST /api/v1/chat/threads/t-mine/messages': jsonResponse(
        { error: { code: 'rate_limited', message: 'Daily cap reached' } },
        429,
      ),
    });
    renderApp('/chat');

    await sendHello(user);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "You've used today's chat messages. Try again tomorrow.",
    );
    // The optimistic bubble is gone with nothing stored, and the text is back to retry.
    expect(screen.queryByText('Plan something', { selector: 'div' })).not.toBeInTheDocument();
    expect(await composer()).toHaveValue('Plan something');
  });

  it('shows the planner message when the planner is unavailable (503)', async () => {
    const user = userEvent.setup();
    chatApi({
      'POST /api/v1/chat/threads/t-mine/messages': jsonResponse(
        { error: { code: 'ai_unavailable', message: 'The planner is resting right now.' } },
        503,
      ),
    });
    renderApp('/chat');

    await sendHello(user);

    expect(await screen.findByRole('alert')).toHaveTextContent('The planner is resting right now.');
  });

  it('Stop aborts the request and drops the streaming reply', async () => {
    const user = userEvent.setup();
    const live = liveSse();
    const api = chatApi({ 'POST /api/v1/chat/threads/t-mine/messages': live.response });
    renderApp('/chat');

    await sendHello(user);
    await act(async () => live.emit('delta', { text: 'Thinking out loud' }));
    // The pending bubble is still waiting for an ack here, so the reply bubble is not shown yet.
    await user.click(await screen.findByRole('button', { name: 'Stop' }));

    const post = api.fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
    expect(post[1]?.signal?.aborted).toBe(true);
    expect(screen.queryByText('Thinking out loud')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
    await user.type(await composer(), 'again');
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled();
  });

  it('aborts the stream when leaving the page', async () => {
    const user = userEvent.setup();
    const live = liveSse();
    const api = chatApi({ 'POST /api/v1/chat/threads/t-mine/messages': live.response });
    const router = renderApp('/chat');

    await sendHello(user);
    await act(async () => router.navigate('/'));

    const post = api.fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
    await vi.waitFor(() => expect(post[1]?.signal?.aborted).toBe(true));
  });
});

describe('ChatPage action cards', () => {
  const withAction = (a: Action) =>
    message({
      id: 'm-reply',
      role: 'assistant',
      content: 'Here is an idea',
      author_is_me: false,
      author_user_id: null,
      actions: [a],
    });

  it('applies a suggestion, marks it added and links to what it created', async () => {
    const user = userEvent.setup();
    const applied = action({ applied_at: '2026-01-01T11:00:00Z', result: { idea_id: 'i9' } });
    const server = store([withAction(action())]);
    const api = chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': server.handler,
      'POST /api/v1/chat/actions/a1/apply': () => {
        server.messages[0] = withAction(applied);
        return { action: applied, idea: { id: 'i9' }, itinerary: null };
      },
    });
    renderApp('/chat');

    expect(await screen.findByText('Pottery night')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Apply' }));

    expect(await screen.findByText('Added ✓')).toBeInTheDocument();
    expect(api.find('POST', '/api/v1/chat/actions/a1/apply')).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'See it in Ideas' })).toHaveAttribute('href', '/ideas');
    expect(screen.queryByRole('button', { name: 'Apply' })).not.toBeInTheDocument();
  });

  it('links an applied itinerary change to the plan', async () => {
    const user = userEvent.setup();
    const change = action({
      id: 'a2',
      type: 'itinerary_change',
      summary: 'Swap dinner',
      payload: { description: 'Move dinner to 8pm' },
    });
    const done = { ...change, applied_at: '2026-01-01T11:00:00Z', result: { itinerary_id: 'it1' } };
    chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': {
        messages: [withAction(change)],
        has_more: false,
      },
      'POST /api/v1/chat/actions/a2/apply': { action: done, idea: null, itinerary: { id: 'it1' } },
    });
    renderApp('/chat');

    expect(await screen.findByText('Move dinner to 8pm')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    expect(await screen.findByRole('link', { name: 'View the plan' })).toHaveAttribute(
      'href',
      '/itineraries/it1',
    );
  });

  it('formats the time of a calendar slot', async () => {
    chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': {
        messages: [
          withAction(
            action({
              id: 'a3',
              type: 'calendar_slot',
              summary: 'Put it on Saturday',
              payload: { starts_at: '2026-03-14T19:30:00Z' },
            }),
          ),
        ],
        has_more: false,
      },
    });
    renderApp('/chat');

    expect(await screen.findByText('Put it on Saturday')).toBeInTheDocument();
    expect(screen.getByText(/Mar/)).toBeInTheDocument();
  });

  it('shows already-applied suggestions as added', async () => {
    chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': {
        messages: [withAction(action({ applied_at: '2026-01-01T11:00:00Z' }))],
        has_more: false,
      },
    });
    renderApp('/chat');

    expect(await screen.findByText('Added ✓')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Apply' })).not.toBeInTheDocument();
  });

  it('says only the planner can change the date on a 403', async () => {
    const user = userEvent.setup();
    chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': {
        messages: [withAction(action())],
        has_more: false,
      },
      'POST /api/v1/chat/actions/a1/apply': jsonResponse(
        { error: { code: 'forbidden', message: 'no' } },
        403,
      ),
    });
    renderApp('/chat');

    await user.click(await screen.findByRole('button', { name: 'Apply' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Only the planner can change this date.',
    );
    expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled();
  });

  it('hides a dismissed card without calling the server', async () => {
    const user = userEvent.setup();
    const api = chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': {
        messages: [withAction(action())],
        has_more: false,
      },
    });
    renderApp('/chat');

    await user.click(await screen.findByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText('Try a pottery class')).not.toBeInTheDocument();
    expect(screen.getByText('Here is an idea')).toBeInTheDocument();
    expect(api.find('POST', '/api/v1/chat/actions/a1/apply')).toHaveLength(0);
  });
});

describe('ChatPage history', () => {
  it('loads earlier messages above the current ones, paging by the oldest id', async () => {
    const user = userEvent.setup();
    let page = 0;
    const api = chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': () => {
        page += 1;
        return page === 1
          ? {
              messages: [
                message({ id: 'm3', content: 'Third' }),
                message({ id: 'm4', content: 'Fourth' }),
              ],
              has_more: true,
            }
          : {
              messages: [
                message({ id: 'm1', content: 'First' }),
                message({ id: 'm2', content: 'Second' }),
              ],
              has_more: false,
            };
      },
    });
    renderApp('/chat');

    expect(await screen.findByText('Fourth')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load earlier' }));

    expect(await screen.findByText('First')).toBeInTheDocument();
    const urls = api.fetchMock.mock.calls
      .map(([input]) => (input instanceof Request ? input.url : String(input)))
      .filter((url) => url.includes('/messages'));
    expect(urls[0]).not.toContain('before=');
    expect(urls[1]).toContain('before=m3');
    const order = screen.getAllByText(/^(First|Second|Third|Fourth)$/).map((el) => el.textContent);
    expect(order).toEqual(['First', 'Second', 'Third', 'Fourth']);
    expect(screen.queryByRole('button', { name: 'Load earlier' })).not.toBeInTheDocument();
  });
});

describe('ChatPage send integrity', () => {
  const reply = message({
    id: 'm-reply',
    role: 'assistant',
    content: 'Sounds lovely',
    author_is_me: false,
    author_user_id: null,
  });

  it('accepts a new message right after done, before the stream closes', async () => {
    const user = userEvent.setup();
    const server = store();
    const live = liveSse();
    let posts = 0;
    const api = chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': server.handler,
      'POST /api/v1/chat/threads/t-mine/messages': () => {
        posts += 1;
        return posts === 1
          ? live.response
          : sseResponse([
              frame('ack', { message: message({ id: 'm-user2', content: 'Second' }) }),
              frame('done', { message_id: 'm-reply2', message: { ...reply, id: 'm-reply2' } }),
            ]);
      },
    });
    renderApp('/chat');

    await sendHello(user, 'First');
    const ackMessage = message({ id: 'm-user', content: 'First' });
    await act(async () => {
      live.emit('ack', { message: ackMessage });
      live.emit('done', { message_id: 'm-reply', message: reply });
    });
    // The connection is still open; the composer is already usable and the send is not lost.
    await user.type(await composer(), 'Second');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(api.find('POST', '/api/v1/chat/threads/t-mine/messages')).toHaveLength(2);
    expect(await composer()).toHaveValue('');
  });

  it('puts the text back when the stream fails before the ack', async () => {
    const user = userEvent.setup();
    const live = liveSse();
    chatApi({ 'POST /api/v1/chat/threads/t-mine/messages': live.response });
    renderApp('/chat');

    await sendHello(user);
    await act(async () => {
      live.emit('error', { code: 'ai_failed', message: 'The planner hiccuped.' });
      live.close();
    });

    expect(await screen.findByRole('alert')).toHaveTextContent('The planner hiccuped.');
    expect(await composer()).toHaveValue('Plan something');
  });

  it('keeps the draft when a send is refused while another is in flight', async () => {
    const user = userEvent.setup();
    const live = liveSse();
    chatApi({ 'POST /api/v1/chat/threads/t-mine/messages': live.response });
    renderApp('/chat');

    await sendHello(user, 'First');
    await user.type(await composer(), 'Second');
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    await user.keyboard('{Enter}');
    expect(await composer()).toHaveValue('Second');
  });

  it('does not show a message twice when a refetch brings it in before the ack', async () => {
    const user = userEvent.setup();
    const server = store();
    const live = liveSse();
    chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': server.handler,
      'POST /api/v1/chat/threads/t-mine/messages': live.response,
    });
    const { queryClient } = renderAppWithClient('/chat');
    await sendHello(user);

    server.messages.push(message({ id: 'm-user', content: 'Plan something' }));
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['chat'] });
    });

    await vi.waitFor(() => expect(screen.queryByText(/\(sending\)/)).not.toBeInTheDocument());
    expect(screen.getAllByText('Plan something')).toHaveLength(1);
  });

  it('does not show the reply twice when a refetch brings it in before done', async () => {
    const user = userEvent.setup();
    const server = store();
    const live = liveSse();
    chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': server.handler,
      'POST /api/v1/chat/threads/t-mine/messages': live.response,
    });
    const { queryClient } = renderAppWithClient('/chat');
    await sendHello(user);
    const ackMessage = message({ id: 'm-user', content: 'Plan something' });
    server.messages.push(ackMessage);
    await act(async () => live.emit('ack', { message: ackMessage }));
    await act(async () => live.emit('delta', { text: 'Sounds lovely' }));
    expect(screen.getByTestId('streaming-reply')).toBeInTheDocument();

    server.messages.push(reply);
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['chat'] });
    });

    await vi.waitFor(() => expect(screen.queryByTestId('streaming-reply')).not.toBeInTheDocument());
    expect(screen.getAllByText('Sounds lovely')).toHaveLength(1);
  });

  it('keeps Send disabled until the conversation history has loaded', async () => {
    const user = userEvent.setup();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const api = chatApi({
      'GET /api/v1/chat/threads/t-mine/messages': async () => {
        await gate;
        return { messages: [], has_more: false };
      },
    });
    renderApp('/chat');

    await user.type(await composer(), 'Too early{Enter}');
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    expect(api.find('POST', '/api/v1/chat/threads/t-mine/messages')).toHaveLength(0);
    expect(await composer()).toHaveValue('Too early');

    await act(async () => release());
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
  });
});
