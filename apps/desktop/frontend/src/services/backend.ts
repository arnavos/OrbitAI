import { invoke, isTauri } from '@tauri-apps/api/core';
import type {
  APIEnvelope,
  AuditEventResponse,
  DetailedHealthResponse,
  HealthResponse,
  SystemInfoResponse,
} from '../types/contracts';

export interface BackendConnectionInfo {
  apiUrl: string;
  wsUrl: string;
  sessionToken: string;
}

/**
 * Check whether code is running inside the native Tauri desktop shell
 * vs standard web browser development mode.
 */
export function isTauriEnvironment(): boolean {
  if (typeof window === 'undefined') return false;
  return isTauri() || '__TAURI_INTERNALS__' in window;
}

export class BackendClient {
  private connection: BackendConnectionInfo | null = null;
  private isInitializing: Promise<BackendConnectionInfo | null> | null = null;
  private connectionListeners: Array<(conn: BackendConnectionInfo) => void> = [];
  private crashListeners: Array<(event: { reason: string; state: string }) => void> = [];

  /**
   * Determine if running in the native desktop container.
   */
  isDesktop(): boolean {
    return isTauriEnvironment();
  }

  /**
   * Return cached connection information if already retrieved.
   */
  getCachedConnection(): BackendConnectionInfo | null {
    return this.connection;
  }

  /**
   * Update cached connection parameters and notify listeners.
   */
  setConnection(info: BackendConnectionInfo) {
    this.connection = info;
    this.connectionListeners.forEach((l) => l(info));
  }

  onConnectionChange(callback: (conn: BackendConnectionInfo) => void) {
    this.connectionListeners.push(callback);
    return () => {
      this.connectionListeners = this.connectionListeners.filter((l) => l !== callback);
    };
  }

  onCrash(callback: (event: { reason: string; state: string }) => void) {
    this.crashListeners.push(callback);
    return () => {
      this.crashListeners = this.crashListeners.filter((l) => l !== callback);
    };
  }

  notifyCrash(event: { reason: string; state: string }) {
    this.crashListeners.forEach((l) => l(event));
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
      headers.set('X-Orbit-Session-Token', conn.sessionToken);
      headers.set('Authorization', `Bearer ${conn.sessionToken}`);
    }
    headers.set('Content-Type', 'application/json');

    const url = `${baseUrl}${path.startsWith('/') ? path : `/${path}`}`;
    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const errorText = await response.text();
      let errorMsg = `API error (${response.status})`;
      try {
        const errorJson = JSON.parse(errorText);
        if (errorJson.error?.message) {
          errorMsg = `${errorMsg}: ${errorJson.error.message}`;
        }
      } catch {
        errorMsg = `${errorMsg}: ${errorText}`;
      }
      throw new Error(errorMsg);
    }

    return response.json();
  }

  /**
   * Probe public or authenticated health check and unwrap envelope response.
   */
  async checkHealth(): Promise<HealthResponse> {
    const res = await this.fetch<APIEnvelope<HealthResponse> | HealthResponse>('/api/v1/health');
    if ('data' in res && res.data) {
      return res.data;
    }
    return res as HealthResponse;
  }

  /**
   * Fetch authenticated detailed system health telemetry.
   */
  async getDetailedHealth(): Promise<DetailedHealthResponse> {
    const res = await this.fetch<APIEnvelope<DetailedHealthResponse>>('/api/v1/health/detailed');
    if (res.data) {
      return res.data;
    }
    throw new Error('Detailed health telemetry unavailable');
  }

  /**
   * Fetch host platform specifications and memory metrics.
   */
  async getSystemInfo(): Promise<SystemInfoResponse> {
    const res = await this.fetch<APIEnvelope<SystemInfoResponse>>('/api/v1/system/info');
    if (res.data) {
      return res.data;
    }
    throw new Error('System information unavailable');
  }

  /**
   * Fetch recent immutable audit events.
   */
  async getAuditEvents(limit = 20): Promise<AuditEventResponse[]> {
    const res = await this.fetch<APIEnvelope<AuditEventResponse[]>>(
      `/api/v1/system/audit?limit=${limit}`
    );
    return res.data || [];
  }
}

export const backendClient = new BackendClient();
