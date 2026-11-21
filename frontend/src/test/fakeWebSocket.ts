/** A controllable stand-in for WebSocket: tests open, push messages and close it by hand. */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static reset() {
    FakeWebSocket.instances = [];
  }

  url: string;
  sent: string[] = [];
  closed = false;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.closed = true;
  }

  open() {
    this.onopen?.(new Event('open'));
  }

  receive(payload: unknown) {
    const data = typeof payload === 'string' ? payload : JSON.stringify(payload);
    this.onmessage?.(new MessageEvent('message', { data }));
  }

  /** The server (or the network) ends the connection. */
  drop(code = 1006) {
    this.onclose?.(new CloseEvent('close', { code }));
  }
}
