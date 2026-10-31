import React, { useEffect, useState } from 'react';
import { Header } from './components/layout/Header';
import { StatusBar } from './components/layout/StatusBar';
import { HostCard } from './components/dashboard/HostCard';
import { BackendCard } from './components/dashboard/BackendCard';
import { DatabaseCard } from './components/dashboard/DatabaseCard';
import { SecurityCard } from './components/dashboard/SecurityCard';
import { AuditFeed } from './components/dashboard/AuditFeed';
import { ErrorBoundary } from './components/ui/ErrorBoundary';
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
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          padding: '24px',
          gap: '16px',
        }}
      >
        <Header status={status} port={port} wsStatus={wsStatus} />

        <main
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: '16px',
          }}
        >
          <HostCard
            systemInfo={activeSystemInfo}
            isDesktop={backendClient.isDesktop()}
          />
          <BackendCard
            telemetry={activeTelemetry}
            systemInfo={activeSystemInfo}
          />
          <DatabaseCard
            telemetry={activeTelemetry}
            systemInfo={activeSystemInfo}
          />
          <SecurityCard
            backendPort={port}
            hasSessionToken={Boolean(connection?.sessionToken)}
          />
        </main>

        <section style={{ flex: 1, minHeight: '120px' }}>
          <AuditFeed
            events={activeAuditEvents}
            isConnected={isConnected}
          />
        </section>

        <StatusBar
          connectionStatus={status}
          wsLatencyMs={latencyMs}
          dbLatencyMs={activeTelemetry?.database_latency_ms ?? null}
          uptimeSeconds={activeTelemetry?.uptime_seconds ?? null}
        />
      </div>
    </ErrorBoundary>
  );
};

export default App;
