import { describe, expect, it } from 'vitest';
import { rootsFor } from './useRealtime';

describe('rootsFor', () => {
  it('refreshes the chat queries when a chat message arrives', () => {
    expect(rootsFor('chat.message')).toContain('chat');
  });

  it('leaves chat alone for other events', () => {
    expect(rootsFor('idea.created')).not.toContain('chat');
  });
});
