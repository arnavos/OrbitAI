import React from 'react';
import { Header } from '../layout/Header';
import { HostCard } from './HostCard';
import { BackendCard } from './BackendCard';
import { DatabaseCard } from './DatabaseCard';
import { SecurityCard } from './SecurityCard';
import { AuditFeed } from './AuditFeed';
import { backendClient } from '../../services/backend';
import type {
  DetailedHealthResponse,
  SystemInfoResponse,
  AuditEventResponse,
  ConnectionStatus,
} from '../../types/contracts';
import type { WebSocketStatus } from '../../services/websocket';

export interface SystemDiagnosticsViewProps {
  status: ConnectionStatus;
  port: string | null;
  wsStatus: WebSocketStatus;
  telemetry: DetailedHealthResponse | null;
  systemInfo: SystemInfoResponse | null;
  auditEvents: AuditEventResponse[];
  hasSessionToken: boolean;
  isConnected: boolean;
}

export const SystemDiagnosticsView: React.FC<SystemDiagnosticsViewProps> = ({
  status,
  port,
  wsStatus,
  telemetry,
  systemInfo,
  auditEvents,
  hasSessionToken,
  isConnected,
}) => {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        padding: '20px',
        gap: '16px',
        overflowY: 'auto',
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
        <HostCard systemInfo={systemInfo} isDesktop={backendClient.isDesktop()} />
        <BackendCard telemetry={telemetry} systemInfo={systemInfo} />
        <DatabaseCard telemetry={telemetry} systemInfo={systemInfo} />
        <SecurityCard backendPort={port} hasSessionToken={hasSessionToken} />
      </main>

      <section style={{ flex: 1, minHeight: '120px' }}>
        <AuditFeed events={auditEvents} isConnected={isConnected} />
      </section>
    </div>
  );
};
