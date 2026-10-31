import type { ServerWebSocketMessage } from '../types/contracts';
import { backendClient } from './backend';

export type WebSocketStatus = 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED' | 'RECONNECTING' | 'ERROR';

export type WebSocketListener = (msg: ServerWebSocketMessage) => void;
export type StatusListener = (status: WebSocketStatus) => void;

export class WebSocketClient {
  private socket: WebSocket | null = null;
  private status: WebSocketStatus = 'DISCONNECTED';
  private listeners: Set<WebSocketListener> = new Set();
  private statusListeners: Set<StatusListener> = new Set();
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private isIntentionallyClosed = false;

  getStatus(): WebSocketStatus {
    return this.status;
  }

  private setStatus(newStatus: WebSocketStatus) {
    if (this.status !== newStatus) {
      this.status = newStatus;
      this.statusListeners.forEach((listener) => listener(newStatus));
    }
  }

  /**
   * Connect to backend WebSocket using Sec-WebSocket-Protocol authentication.
   * Avoids transmitting tokens in URL query parameters.
   */
  async connect(): Promise<void> {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const conn = await backendClient.getConnection();
    if (!conn) {
      this.setStatus('DISCONNECTED');
      return;
    }

    this.isIntentionallyClosed = false;
    this.setStatus(this.reconnectAttempts > 0 ? 'RECONNECTING' : 'CONNECTING');

    try {
      // Sec-WebSocket-Protocol handshake: ['orbit-auth', '<sessionToken>']
      // URL has zero query parameters: ws://127.0.0.1:<port>/api/v1/ws
      const cleanUrl = conn.wsUrl.split('?')[0];
      const ws = new WebSocket(cleanUrl, ['orbit-auth', conn.sessionToken]);
      this.socket = ws;

      ws.onopen = () => {
        this.setStatus('CONNECTED');
        this.reconnectAttempts = 0;
        this.startHeartbeat();
        // Request initial state snapshot over the authenticated channel
        this.sendAction('get_telemetry');
        this.sendAction('get_system_info');
        this.sendAction('get_audit_events');
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as ServerWebSocketMessage;
          this.listeners.forEach((listener) => listener(msg));
        } catch (e) {
          console.warn('Failed to parse WebSocket message frame:', e);
        }
      };

      ws.onerror = (err) => {
        console.warn('WebSocket connection error encountered:', err);
        this.setStatus('ERROR');
      };

      ws.onclose = () => {
        this.stopHeartbeat();
        this.socket = null;
        if (!this.isIntentionallyClosed) {
          this.scheduleReconnect();
        } else {
          this.setStatus('DISCONNECTED');
        }
      };
    } catch (err) {
      console.warn('Failed to instantiate WebSocket:', err);
      this.setStatus('ERROR');
      this.scheduleReconnect();
    }
  }

  /**
   * Schedule exponential backoff reconnection.
   */
  private scheduleReconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }
    this.setStatus('RECONNECTING');

    // Exponential backoff: 500ms * 1.5^n, capped at 5000ms
    const baseDelay = 500;
    const maxDelay = 5000;
    const delay = Math.min(baseDelay * Math.pow(1.5, this.reconnectAttempts), maxDelay);
    this.reconnectAttempts++;

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  /**
   * Keep-alive ping mechanism.
   */
  private startHeartbeat() {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.socket && this.socket.readyState === WebSocket.OPEN) {
        this.sendAction('ping');
      }
    }, 10000);
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  /**
   * Send typed action command to backend.
   */
  sendAction(action: 'ping' | 'get_telemetry' | 'get_system_info' | 'get_audit_events') {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ action }));
    }
  }

  /**
   * Disconnect and cancel automatic reconnection.
   */
  disconnect() {
    this.isIntentionallyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.stopHeartbeat();
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.setStatus('DISCONNECTED');
  }

  subscribe(listener: WebSocketListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  subscribeStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => {
      this.statusListeners.delete(listener);
    };
  }
}

export const webSocketClient = new WebSocketClient();

// Reconnect WebSocket automatically when backend restarts on a fresh port and token
backendClient.onConnectionChange(() => {
  webSocketClient.disconnect();
  webSocketClient.connect();
});
