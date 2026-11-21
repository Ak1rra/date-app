import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import { FakeWebSocket } from './fakeWebSocket';

// No test may open a real socket: signed-in pages mount the realtime provider.
beforeEach(() => {
  FakeWebSocket.reset();
  globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
});

afterEach(() => {
  cleanup();
});
