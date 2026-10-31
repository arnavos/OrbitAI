import { useEffect, useState } from 'react';
import {
  webSocketClient,
  type WebSocketStatus,
} from '../services/websocket';
import type {
  AuditEventResponse,
  DetailedHealthResponse,
  ServerWebSocketMessage,
  SystemInfoResponse,
} from '../types/contracts';

export interface UseWebSocketReturn {
  wsStatus: WebSocketStatus;
  telemetry: DetailedHealthResponse | null;
  systemInfo: SystemInfoResponse | null;
  auditEvents: AuditEventResponse[];
  latencyMs: number | null;
  refreshTelemetry: () => void;
}

export function useWebSocket(enabled: boolean): UseWebSocketReturn {
  const [wsStatus, setWsStatus] = useState<WebSocketStatus>(webSocketClient.getStatus());
  const [telemetry, setTelemetry] = useState<DetailedHealthResponse | null>(null);
  const [systemInfo, setSystemInfo] = useState<SystemInfoResponse | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEventResponse[]>([]);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    const unsubStatus = webSocketClient.subscribeStatus((status) => {
      setWsStatus(status);
    });

    let pingTimestamp = 0;

    const unsubMessages = webSocketClient.subscribe((msg: ServerWebSocketMessage) => {
      if (msg.type === 'TELEMETRY_UPDATE') {
        setTelemetry(msg.data);
      } else if (msg.type === 'SYSTEM_INFO') {
        setSystemInfo(msg.data);
      } else if (msg.type === 'AUDIT_EVENTS') {
        setAuditEvents(msg.data);
      } else if (msg.type === 'pong') {
        if (pingTimestamp > 0) {
          setLatencyMs(Math.round(performance.now() - pingTimestamp));
        }
      }
    });

    webSocketClient.connect();

    // Ping every 5 seconds to update roundtrip latency and request telemetry
    const pollInterval = setInterval(() => {
      if (webSocketClient.getStatus() === 'CONNECTED') {
        pingTimestamp = performance.now();
        webSocketClient.sendAction('ping');
        webSocketClient.sendAction('get_telemetry');
      }
    }, 4000);

    return () => {
      unsubStatus();
      unsubMessages();
      clearInterval(pollInterval);
    };
  }, [enabled]);

  const refreshTelemetry = () => {
    webSocketClient.sendAction('get_telemetry');
    webSocketClient.sendAction('get_system_info');
    webSocketClient.sendAction('get_audit_events');
  };

  return {
    wsStatus,
    telemetry,
    systemInfo,
    auditEvents,
    latencyMs,
    refreshTelemetry,
  };
}
