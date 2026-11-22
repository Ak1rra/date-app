import { describe, expect, it } from 'vitest';
import { rootsFor } from './useRealtime';

describe('rootsFor', () => {
  it('refreshes the chat queries when a chat message arrives', () => {
    expect(rootsFor('chat.message')).toContain('chat');
  });

  it('refreshes ratings and past dates when a date completes or a rating is revealed', () => {
    expect(rootsFor('date.completed')).toEqual(expect.arrayContaining(['ratings', 'past-dates']));
    expect(rootsFor('rating.revealed')).toEqual(expect.arrayContaining(['ratings', 'past-dates']));
  });

  it('leaves ratings alone for other events', () => {
    expect(rootsFor('idea.created')).not.toContain('ratings');
    expect(rootsFor('date.scheduled')).not.toContain('ratings');
  });

  it('leaves chat alone for other events', () => {
    expect(rootsFor('idea.created')).not.toContain('chat');
  });
});
