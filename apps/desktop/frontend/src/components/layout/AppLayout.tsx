import React, { useEffect } from 'react';
import { useChat } from '../../context/ChatContext';
import { TitleBar } from './TitleBar';
import { Sidebar } from '../sidebar/Sidebar';
import { ChatWorkspace } from '../chat/ChatWorkspace';
import { SystemDiagnosticsView } from '../dashboard/SystemDiagnosticsView';
import { StatusBar } from './StatusBar';
import type {
  DetailedHealthResponse,
  SystemInfoResponse,
  AuditEventResponse,
  ConnectionStatus,
} from '../../types/contracts';
import type { WebSocketStatus } from '../../services/websocket';

export interface AppLayoutProps {
  status: ConnectionStatus;
  port: string | null;
  wsStatus: WebSocketStatus;
  wsLatencyMs: number | null;
  telemetry: DetailedHealthResponse | null;
  systemInfo: SystemInfoResponse | null;
  auditEvents: AuditEventResponse[];
  hasSessionToken: boolean;
  isConnected: boolean;
}

export const AppLayout: React.FC<AppLayoutProps> = ({
  status,
  port,
  wsStatus,
  wsLatencyMs,
  telemetry,
  systemInfo,
  auditEvents,
  hasSessionToken,
  isConnected,
}) => {
  const { state, toggleSidebar, createConversation } = useChat();

  // Keyboard shortcut listeners (Cmd+N, Cmd+B)
  useEffect(() => {
    const handleGlobalKeys = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'b') {
        e.preventDefault();
        toggleSidebar();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'n') {
        e.preventDefault();
        createConversation('New Conversation');
      }
    };
    window.addEventListener('keydown', handleGlobalKeys);
    return () => window.removeEventListener('keydown', handleGlobalKeys);
  }, [toggleSidebar, createConversation]);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        width: '100vw',
        overflow: 'hidden',
        backgroundColor: 'var(--bg-primary)',
      }}
    >
      <TitleBar connectionStatus={status} port={port} />

      <div
        style={{
          display: 'flex',
          flex: 1,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        <Sidebar />

        {/* Chat workspace view */}
        <div
          style={{
            display: state.activeView === 'chat' ? 'flex' : 'none',
            flex: 1,
            overflow: 'hidden',
            flexDirection: 'column',
          }}
        >
          <ChatWorkspace />
        </div>

        {/* System Diagnostics view (M0 Foundation dashboard) */}
        <div
          style={{
            display: state.activeView === 'system' ? 'flex' : 'none',
            flex: 1,
            overflow: 'hidden',
            flexDirection: 'column',
          }}
        >
          <SystemDiagnosticsView
            status={status}
            port={port}
            wsStatus={wsStatus}
            telemetry={telemetry}
            systemInfo={systemInfo}
            auditEvents={auditEvents}
            hasSessionToken={hasSessionToken}
            isConnected={isConnected}
          />
        </div>
      </div>

      <StatusBar
        connectionStatus={status}
        wsLatencyMs={wsLatencyMs}
        dbLatencyMs={telemetry?.database_latency_ms ?? null}
        uptimeSeconds={telemetry?.uptime_seconds ?? null}
      />
    </div>
  );
};
