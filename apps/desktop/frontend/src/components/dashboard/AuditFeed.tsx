import React from 'react';
import { ScrollText } from 'lucide-react';
import { Card } from '../ui/Card';
import { Badge, type BadgeVariant } from '../ui/Badge';
import type { AuditEventResponse } from '../../types/contracts';

export interface AuditFeedProps {
  events: AuditEventResponse[];
  isConnected: boolean;
}

export const AuditFeed: React.FC<AuditFeedProps> = ({ events, isConnected }) => {
  const getSeverityVariant = (sev: string): BadgeVariant => {
    switch (sev.toUpperCase()) {
      case 'CRITICAL':
      case 'ERROR':
        return 'error';
      case 'WARN':
      case 'WARNING':
        return 'warning';
      case 'INFO':
        return 'info';
      default:
        return 'idle';
    }
  };

  const formatTimestamp = (isoStr: string): string => {
    try {
      const d = new Date(isoStr);
      return d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return isoStr;
    }
  };

  return (
    <Card
      title="Audit Event Stream"
      icon={<ScrollText size={18} />}
      badgeText={isConnected ? `${events.length} EVENTS` : 'DISCONNECTED'}
      badgeVariant={isConnected ? 'healthy' : 'idle'}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
          maxHeight: '160px',
          overflowY: 'auto',
          paddingRight: '4px',
        }}
      >
        {events.length === 0 ? (
          <div
            style={{
              padding: '16px',
              textAlign: 'center',
              color: 'var(--text-muted)',
              fontSize: '12px',
              fontStyle: 'italic',
            }}
          >
            {isConnected ? 'No audit events recorded yet.' : 'Audit stream unavailable (backend offline).'}
          </div>
        ) : (
          events.map((evt) => (
            <div
              key={evt.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '8px 12px',
                background: 'rgba(255, 255, 255, 0.02)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius-sm)',
                fontSize: '11px',
                fontFamily: 'var(--font-mono)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Badge
                  label={evt.severity}
                  variant={getSeverityVariant(evt.severity)}
                />
                <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                  {evt.event_type}
                </span>
                <span style={{ color: 'var(--text-muted)' }}>
                  by [{evt.actor}]
                </span>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', color: 'var(--text-secondary)' }}>
                <span>ID: {evt.correlation_id ? evt.correlation_id.slice(0, 8) : evt.id}</span>
                <span>{formatTimestamp(evt.created_at)}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </Card>
  );
};
