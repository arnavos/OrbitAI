import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { WebSocketClient, type WebSocketStatus } from '../websocket';
import { backendClient } from '../backend';

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  url: string;
  protocols: string | string[] | undefined;
  readyState: number = MockWebSocket.CONNECTING;
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;

  static instances: MockWebSocket[] = [];

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
    MockWebSocket.instances.push(this);
  }

  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = 3; // CLOSED
    if (this.onclose) this.onclose({});
  });

  // Test helpers to simulate server events
  simulateOpen() {
    this.readyState = 1; // OPEN
    if (this.onopen) this.onopen({});
  }

  simulateMessage(data: unknown) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(data) });
  }

  simulateError(err: unknown) {
    if (this.onerror) this.onerror(err);
  }

  simulateClose() {
    this.readyState = 3;
    if (this.onclose) this.onclose({});
  }
}

describe('WebSocketClient — Protocol Handshake, Zero-URL Auth & Lifecycle', () => {
  const originalWebSocket = global.WebSocket;
  let client: WebSocketClient;

  beforeEach(() => {
    MockWebSocket.instances = [];
    // @ts-expect-error Mocking WebSocket constructor
    global.WebSocket = MockWebSocket;
    vi.useFakeTimers();

    client = new WebSocketClient();

    // Mock backendClient.getConnection() to return valid parameters
    vi.spyOn(backendClient, 'getConnection').mockResolvedValue({
      apiUrl: 'http://127.0.0.1:51234',
      wsUrl: 'ws://127.0.0.1:51234/api/v1/ws',
      sessionToken: 'test-session-secret-hmac-9876',
    });
  });

  afterEach(() => {
    client.disconnect();
    global.WebSocket = originalWebSocket;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('connects using Sec-WebSocket-Protocol without query parameters in URL', async () => {
    const connectPromise = client.connect();
    await vi.runAllTimersAsync();
    await connectPromise;

    expect(MockWebSocket.instances.length).toBe(1);
    const ws = MockWebSocket.instances[0];

    // URL must be clean with NO credentials or query params
    expect(ws.url).toBe('ws://127.0.0.1:51234/api/v1/ws');
    expect(ws.url).not.toContain('test-session-secret-hmac-9876');
    expect(ws.url).not.toContain('token=');

    // Authentication token must be passed in subprotocol array
    expect(ws.protocols).toEqual(['orbit-auth', 'test-session-secret-hmac-9876']);
  });

  it('updates status to CONNECTED and requests initial snapshots upon open', async () => {
    const statuses: WebSocketStatus[] = [];
    client.subscribeStatus((s) => statuses.push(s));

    const connectPromise = client.connect();
    await vi.runAllTimersAsync();
    await connectPromise;

    const ws = MockWebSocket.instances[0];
    ws.simulateOpen();

    expect(client.getStatus()).toBe('CONNECTED');
    expect(statuses).toContain('CONNECTING');
    expect(statuses).toContain('CONNECTED');

    // Verify initial state snapshot requests sent over authenticated channel
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ action: 'get_telemetry' }));
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ action: 'get_system_info' }));
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ action: 'get_audit_events' }));
  });

  it('dispatches parsed server frames to subscribers', async () => {
    const receivedMessages: unknown[] = [];
    client.subscribe((msg) => receivedMessages.push(msg));

    const connectPromise = client.connect();
    await vi.runAllTimersAsync();
    await connectPromise;

    const ws = MockWebSocket.instances[0];
    ws.simulateOpen();

    const mockTelemetryUpdate = {
      type: 'TELEMETRY_UPDATE',
      data: {
        status: 'healthy',
        version: '0.1.0',
        uptime_seconds: 50,
        pid: 9999,
        memory_rss_mb: 48.0,
        cpu_percent: 1.0,
        database_connected: true,
        database_latency_ms: 0.2,
        journal_mode: 'wal',
      },
    };

    ws.simulateMessage(mockTelemetryUpdate);
    expect(receivedMessages).toContainEqual(mockTelemetryUpdate);
  });

  it('schedules exponential backoff reconnection on unexpected disconnection', async () => {
    const statuses: WebSocketStatus[] = [];
    client.subscribeStatus((s) => statuses.push(s));

    const connectPromise = client.connect();
    await vi.runAllTimersAsync();
    await connectPromise;

    const ws1 = MockWebSocket.instances[0];
    ws1.simulateOpen();
    expect(client.getStatus()).toBe('CONNECTED');

    // Simulate backend server disconnect / restart
    ws1.simulateClose();
    expect(client.getStatus()).toBe('RECONNECTING');

    // Advance clock to trigger reconnect attempt
    await vi.advanceTimersByTimeAsync(600);

    // Second WebSocket instance should now be instantiated
    expect(MockWebSocket.instances.length).toBe(2);
    const ws2 = MockWebSocket.instances[1];
    ws2.simulateOpen();
    expect(client.getStatus()).toBe('CONNECTED');
  });

  it('does not auto-reconnect when disconnect() is called intentionally', async () => {
    const connectPromise = client.connect();
    await vi.runAllTimersAsync();
    await connectPromise;

    const ws = MockWebSocket.instances[0];
    ws.simulateOpen();

    client.disconnect();
    expect(client.getStatus()).toBe('DISCONNECTED');

    // Advance timer past reconnect backoff
    await vi.advanceTimersByTimeAsync(10000);

    // No new instance should be created
    expect(MockWebSocket.instances.length).toBe(1);
  });
});
