const encoder = new TextEncoder();

/** One server-sent event frame, as the chat endpoint writes it. */
export const frame = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

const headers = { 'Content-Type': 'text/event-stream' };

/** A finished text/event-stream response made of the given chunks. */
export function sseResponse(chunks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers });
}

/** A text/event-stream response that stays open: the test emits frames and closes it itself. */
export function liveSse() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: new Response(body, { status: 200, headers }),
    emit: (event: string, data: unknown) => controller.enqueue(encoder.encode(frame(event, data))),
    close: () => controller.close(),
  };
}
