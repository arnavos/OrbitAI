import { invoke, isTauri } from '@tauri-apps/api/core';

export interface BackendConnectionInfo {
  apiUrl: string;
  wsUrl: string;
  sessionToken: string;
}

export interface HealthResponse {
  status: string;
  version: string;
  uptime_seconds?: number;
  uptimeSeconds?: number;
}

export interface ApiResponse<T> {
  success: boolean;
  data: T;
  error?: string | null;
  correlation_id?: string;
  timestamp?: string;
}

/**
 * Check whether code is running inside the native Tauri desktop shell
 * vs standard web browser development mode.
 */
export function isTauriEnvironment(): boolean {
  if (typeof window === 'undefined') return false;
  return isTauri() || '__TAURI_INTERNALS__' in window;
}

class BackendClient {
  private connection: BackendConnectionInfo | null = null;
  private isInitializing: Promise<BackendConnectionInfo | null> | null = null;

  /**
   * Determine if running in the native desktop container.
   */
  isDesktop(): boolean {
    return isTauriEnvironment();
  }

  /**
   * Securely retrieve backend connection parameters via Tauri IPC.
   * If running in standalone browser Vite dev mode, returns null immediately.
   * If running in Tauri desktop, retries with backoff while the sidecar completes startup.
   */
  async getConnection(maxRetries = 10, retryDelayMs = 400): Promise<BackendConnectionInfo | null> {
    if (this.connection) {
      return this.connection;
    }

    if (!this.isDesktop()) {
      return null;
    }

    if (this.isInitializing) {
      return this.isInitializing;
    }

    this.isInitializing = (async () => {
      for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
          const info = await invoke<BackendConnectionInfo>('get_backend_connection');
          if (info && info.apiUrl && info.sessionToken) {
            this.connection = info;
            return info;
          }
        } catch (err) {
          if (attempt === maxRetries) {
            console.error('Failed to retrieve backend connection via Tauri IPC:', err);
          } else {
            await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
          }
        }
      }
      return null;
    })();

    const result = await this.isInitializing;
    this.isInitializing = null;
    return result;
  }

  /**
   * Execute authenticated HTTP request against local backend.
   * Session token is strictly passed via HTTP header, never in URL parameters.
   */
  async fetch<T>(path: string, options: RequestInit = {}): Promise<T> {
    const conn = await this.getConnection();
    const baseUrl = conn?.apiUrl || 'http://127.0.0.1:8000';
    const headers = new Headers(options.headers || {});

    if (conn?.sessionToken) {
      headers.set('X-Session-Token', conn.sessionToken);
    }
    headers.set('Content-Type', 'application/json');

    const url = `${baseUrl}${path.startsWith('/') ? path : `/${path}`}`;
    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`API error (${response.status}): ${errorText}`);
    }

    return response.json();
  }

  /**
   * Probe backend health check and unwrap envelope response.
   */
  async checkHealth(): Promise<HealthResponse> {
    const res = await this.fetch<ApiResponse<HealthResponse> | HealthResponse>('/api/v1/health');
    if ('data' in res && res.data) {
      return res.data;
    }
    return res as HealthResponse;
  }
}

export const backendClient = new BackendClient();
