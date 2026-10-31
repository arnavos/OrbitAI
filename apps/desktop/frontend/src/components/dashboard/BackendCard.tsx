import React from 'react';
import { Cpu } from 'lucide-react';
import { Card } from '../ui/Card';
import { Metric } from '../ui/Metric';
import type { DetailedHealthResponse, SystemInfoResponse } from '../../types/contracts';

export interface BackendCardProps {
  telemetry: DetailedHealthResponse | null;
  systemInfo: SystemInfoResponse | null;
}

export const BackendCard: React.FC<BackendCardProps> = ({ telemetry, systemInfo }) => {
  const formatUptime = (seconds: number | undefined): string | null => {
    if (seconds === undefined || seconds === null) return null;
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}m ${secs}s`;
  };

  const isHealthy = telemetry?.status === 'healthy';
  const badgeVariant = telemetry
    ? isHealthy
      ? 'healthy'
      : 'error'
    : 'idle';

  return (
    <Card
      title="Python 3.13 Backend"
      icon={<Cpu size={18} />}
      badgeText={telemetry ? telemetry.status.toUpperCase() : 'OFFLINE'}
      badgeVariant={badgeVariant}
    >
      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: '16px' }}>
        FastAPI local daemon managed with uv. Exclusively bound to 127.0.0.1 with session-token authentication.
      </p>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: '12px',
          marginBottom: '16px',
        }}
      >
        <Metric
          label="Process ID (PID)"
          value={telemetry?.pid ?? null}
        />
        <Metric
          label="Process RSS Memory"
          value={telemetry?.memory_rss_mb ?? null}
          unit="MB"
        />
        <Metric
          label="CPU Utilization"
          value={telemetry?.cpu_percent ?? null}
          unit="%"
        />
        <Metric
          label="Process Uptime"
          value={formatUptime(telemetry?.uptime_seconds)}
        />
      </div>

      <div
        style={{
          marginTop: 'auto',
          fontSize: '11px',
          fontFamily: 'var(--font-mono)',
          color: 'var(--text-muted)',
          display: 'flex',
          justifyContent: 'space-between',
        }}
      >
        <span>
          Runtime: {systemInfo?.python_version ? `Python ${systemInfo.python_version}` : 'Pinned Python 3.13.9'}
        </span>
        <span>Version: {telemetry?.version ? `v${telemetry.version}` : 'UNAVAILABLE'}</span>
      </div>
    </Card>
  );
};
