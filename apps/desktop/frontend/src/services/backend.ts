import { invoke } from '@tauri-apps/api/core';

export interface BackendConnectionInfo {
  apiUrl: string;
  wsUrl: string;
  sessionToken: string;
}

export interface HealthResponse {
  status: string;
  version: string;
  uptimeSeconds: number;
}

class BackendClient {
  private connection: BackendConnectionInfo | null = null;
  private isInitializing: Promise<BackendConnectionInfo | null> | null = null;

  /**
   * Securely retrieve backend connection parameters via Tauri IPC.
   * If running in standalone browser Vite dev mode, falls back cleanly.
   */
  async getConnection(): Promise<BackendConnectionInfo | null> {
    if (this.connection) {
      return this.connection;
    }

    if (this.isInitializing) {
      return this.isInitializing;
    }

    this.isInitializing = (async () => {
      try {
        if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
          const info = await invoke<BackendConnectionInfo>('get_backend_connection');
          this.connection = info;
          return info;
        }
      } catch {
        // Never log session tokens or sensitive payload in error outputs
        console.warn('Backend IPC connection not ready or unavailable.');
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
   * Probe backend health check.
   */
  async checkHealth(): Promise<HealthResponse> {
    return this.fetch<HealthResponse>('/api/v1/health');
  }
}

export const backendClient = new BackendClient();
