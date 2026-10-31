import React from 'react';
import { Badge, type BadgeVariant } from '../ui/Badge';
import type { ConnectionStatus } from '../../types/contracts';
import type { WebSocketStatus } from '../../services/websocket';

export interface HeaderProps {
  status: ConnectionStatus;
  port: string | null;
  wsStatus: WebSocketStatus;
}

export const Header: React.FC<HeaderProps> = ({ status, port, wsStatus }) => {
  const getBadgeVariant = (s: ConnectionStatus): BadgeVariant => {
    switch (s) {
      case 'CONNECTED':
        return 'healthy';
      case 'CONNECTING':
      case 'INITIALIZING':
      case 'RECONNECTING':
        return 'warning';
      case 'HEALTH CHECK FAILED':
      case 'INITIALIZATION TIMEOUT':
        return 'error';
      case 'STANDALONE DEV':
      default:
        return 'info';
    }
  };

  const getWsBadgeVariant = (ws: WebSocketStatus): BadgeVariant => {
    switch (ws) {
      case 'CONNECTED':
        return 'healthy';
      case 'CONNECTING':
      case 'RECONNECTING':
        return 'warning';
      case 'ERROR':
        return 'error';
      case 'DISCONNECTED':
      default:
        return 'idle';
    }
  };

  const badgeText = `STAGE 0.5: ${status}${port ? ` [PORT ${port}]` : ''}`;

  return (
    <header
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        paddingBottom: '16px',
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      <div>
        <h1
          style={{
            fontSize: '20px',
            fontWeight: 600,
            letterSpacing: '-0.02em',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
          }}
        >
          <span style={{ color: 'var(--accent-primary)', fontSize: '24px' }}>⬡</span> ORBIT
          <span
            style={{
              fontSize: '11px',
              padding: '2px 6px',
              background: 'var(--bg-tertiary)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-muted)',
            }}
          >
            M0 Foundation
          </span>
        </h1>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
          Engineering Architecture & Real-Time Diagnostics
        </p>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <Badge
          label={`WS: ${wsStatus}`}
          variant={getWsBadgeVariant(wsStatus)}
          pulse={wsStatus === 'CONNECTED'}
        />
        <Badge
          label={badgeText}
          variant={getBadgeVariant(status)}
          pulse={status === 'CONNECTED'}
        />
      </div>
    </header>
  );
};
