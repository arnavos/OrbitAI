import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { BackendClient } from '../backend';

describe('BackendClient — API Response Envelope & Structured Errors', () => {
  let client: BackendClient;
  const originalFetch = global.fetch;

  beforeEach(() => {
    client = new BackendClient();
    // Simulate active connection in memory closure
    (client as unknown as { connection: { apiUrl: string; sessionToken: string } | null }).connection = {
      apiUrl: 'http://127.0.0.1:49999',
      sessionToken: 'test-secret-token-xyz',
    };
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('unwraps successful API response envelope for basic health', async () => {
    const mockPayload = {
      success: true,
      data: {
        status: 'healthy',
        version: '0.1.0',
        uptime_seconds: 120.5,
      },
      correlation_id: 'corr-test-1',
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify(mockPayload),
      json: async () => mockPayload,
    } as unknown as Response);

    const result = await client.checkHealth();
    expect(result).toEqual(mockPayload.data);
    expect(global.fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:49999/api/v1/health',
      expect.objectContaining({
        headers: expect.any(Headers),
      })
    );
  });

  it('unwraps successful API response envelope for detailed health telemetry', async () => {
    const mockTelemetry = {
      status: 'healthy',
      version: '0.1.0',
      uptime_seconds: 240,
      pid: 1234,
      memory_rss_mb: 45.2,
      cpu_percent: 1.5,
      database_connected: true,
      database_latency_ms: 0.35,
      journal_mode: 'wal',
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ success: true, data: mockTelemetry }),
      json: async () => ({
        success: true,
        data: mockTelemetry,
        correlation_id: 'corr-test-2',
      }),
    } as unknown as Response);

    const result = await client.getDetailedHealth();
    expect(result).toEqual(mockTelemetry);
  });

  it('extracts structured error message when success is false', async () => {
    const errorPayload = {
      success: false,
      data: null,
      error: {
        code: 'AUTHENTICATION_FAILED',
        message: 'Invalid or expired session token.',
      },
      correlation_id: 'corr-err-1',
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      text: async () => JSON.stringify(errorPayload),
      json: async () => errorPayload,
    } as unknown as Response);

    await expect(client.getDetailedHealth()).rejects.toThrow(
      'Invalid or expired session token.'
    );
  });

  it('handles server errors with missing structured error body gracefully', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      text: async () => 'Bad Gateway upstream',
      json: async () => {
        throw new Error('Not JSON');
      },
    } as unknown as Response);

    await expect(client.checkHealth()).rejects.toThrow('API error (502): Bad Gateway upstream');
  });

  it('handles complete network failure (backend unreachable)', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('Connection refused'));

    await expect(client.getDetailedHealth()).rejects.toThrow('Connection refused');
  });
});
