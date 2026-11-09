import React, { useEffect, useState } from 'react';
import { ErrorBoundary } from './components/ui/ErrorBoundary';
import { ThemeProvider } from './context/ThemeContext';
import { ChatProvider } from './context/ChatContext';
import { AppLayout } from './components/layout/AppLayout';
import { useBackend } from './hooks/useBackend';
import { useWebSocket } from './hooks/useWebSocket';
import { backendClient } from './services/backend';
import type {
  DetailedHealthResponse,
  SystemInfoResponse,
  AuditEventResponse,
} from './types/contracts';

export const App: React.FC = () => {
  const { status, connection, port } = useBackend();
  const isConnected = status === 'CONNECTED';
  const {
    wsStatus,
    telemetry: wsTelemetry,
    systemInfo: wsSystemInfo,
    auditEvents: wsAuditEvents,
    latencyMs,
  } = useWebSocket(isConnected);

  // Local state for HTTP fallback and initial fast render
  const [httpTelemetry, setHttpTelemetry] = useState<DetailedHealthResponse | null>(null);
  const [httpSystemInfo, setHttpSystemInfo] = useState<SystemInfoResponse | null>(null);
  const [httpAuditEvents, setHttpAuditEvents] = useState<AuditEventResponse[]>([]);

  // Initial HTTP probe when connection is established
  useEffect(() => {
    let active = true;
    if (isConnected) {
      Promise.all([
        backendClient.getDetailedHealth().catch(() => null),
        backendClient.getSystemInfo().catch(() => null),
        backendClient.getAuditEvents().catch(() => []),
      ]).then(([telemetry, sysInfo, audits]) => {
        if (!active) return;
        if (telemetry) setHttpTelemetry(telemetry);
        if (sysInfo) setHttpSystemInfo(sysInfo);
        if (audits && audits.length > 0) setHttpAuditEvents(audits);
      });
    }
    return () => {
      active = false;
    };
  }, [isConnected]);

  // Merge real telemetry: prefer live WS updates, fall back to initial HTTP probe
  const activeTelemetry = wsTelemetry ?? httpTelemetry;
  const activeSystemInfo = wsSystemInfo ?? httpSystemInfo;
  const activeAuditEvents = wsAuditEvents.length > 0 ? wsAuditEvents : httpAuditEvents;

  return (
    <ErrorBoundary>
      <ThemeProvider>
        <ChatProvider>
          <AppLayout
            status={status}
            port={port}
            wsStatus={wsStatus}
            wsLatencyMs={latencyMs}
            telemetry={activeTelemetry}
            systemInfo={activeSystemInfo}
            auditEvents={activeAuditEvents}
            hasSessionToken={Boolean(connection?.sessionToken)}
            isConnected={isConnected}
          />
        </ChatProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
};

export default App;
