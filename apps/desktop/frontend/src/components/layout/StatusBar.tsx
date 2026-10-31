import React from 'react';
import { Activity, Wifi, HardDrive } from 'lucide-react';
import type { ConnectionStatus } from '../../types/contracts';

export interface StatusBarProps {
  connectionStatus: ConnectionStatus;
  wsLatencyMs: number | null;
  dbLatencyMs: number | null;
  uptimeSeconds: number | null;
}

export const StatusBar: React.FC<StatusBarProps> = ({
  connectionStatus,
  wsLatencyMs,
  dbLatencyMs,
  uptimeSeconds,
}) => {
  const formatUptime = (seconds: number | null): string => {
    if (seconds === null || seconds === undefined) return 'UNAVAILABLE';
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    if (hrs > 0) return `${hrs}h ${mins}m ${secs}s`;
    if (mins > 0) return `${mins}m ${secs}s`;
    return `${secs}s`;
  };

  const getSystemStatusLabel = (): { label: string; color: string } => {
    switch (connectionStatus) {
      case 'CONNECTED':
        return { label: 'ONLINE', color: 'var(--status-healthy)' };
      case 'CONNECTING':
      case 'INITIALIZING':
      case 'RECONNECTING':
        return { label: connectionStatus, color: 'var(--status-warning)' };
      case 'HEALTH CHECK FAILED':
      case 'INITIALIZATION TIMEOUT':
        return { label: 'DEGRADED', color: 'var(--status-error)' };
      case 'STANDALONE DEV':
      default:
        return { label: 'STANDALONE DEV', color: 'var(--accent-primary)' };
    }
  };

  const status = getSystemStatusLabel();

  return (
    <footer
      className="glass-panel"
      style={{
        padding: '10px 16px',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        fontSize: '12px',
        marginTop: 'auto',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Activity size={14} style={{ color: status.color }} />
          <span style={{ color: 'var(--text-secondary)' }}>System Status:</span>
          <span
            style={{
              fontWeight: 600,
              color: status.color,
              fontFamily: 'var(--font-mono)',
            }}
          >
            {status.label}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-muted)' }}>
          <Wifi size={13} />
          <span>WS Latency:</span>
          <span style={{ fontFamily: 'var(--font-mono)', color: wsLatencyMs !== null ? 'var(--text-primary)' : 'var(--text-muted)' }}>
            {wsLatencyMs !== null ? `${wsLatencyMs}ms` : 'UNAVAILABLE'}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-muted)' }}>
          <HardDrive size={13} />
          <span>DB Latency:</span>
          <span style={{ fontFamily: 'var(--font-mono)', color: dbLatencyMs !== null ? 'var(--text-primary)' : 'var(--text-muted)' }}>
            {dbLatencyMs !== null ? `${dbLatencyMs}ms` : 'UNAVAILABLE'}
          </span>
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          gap: '16px',
          color: 'var(--text-muted)',
          fontFamily: 'var(--font-mono)',
          fontSize: '11px',
        }}
      >
        <span>UPTIME: {formatUptime(uptimeSeconds)}</span>
        <span>MEMORY BUDGET: 16 GB</span>
        <span>COST: ₹0/MO</span>
      </div>
    </footer>
  );
};
