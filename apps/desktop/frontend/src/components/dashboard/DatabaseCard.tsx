import React from 'react';
import { HardDrive } from 'lucide-react';
import { Card } from '../ui/Card';
import { Metric } from '../ui/Metric';
import type { DetailedHealthResponse, SystemInfoResponse } from '../../types/contracts';

export interface DatabaseCardProps {
  telemetry: DetailedHealthResponse | null;
  systemInfo: SystemInfoResponse | null;
}

export const DatabaseCard: React.FC<DatabaseCardProps> = ({ telemetry, systemInfo }) => {
  const isConnected = telemetry?.database_connected;
  const badgeText = telemetry
    ? isConnected
      ? 'WAL ACTIVE'
      : 'DEGRADED'
    : 'OFFLINE';
  const badgeVariant = telemetry
    ? isConnected
      ? 'healthy'
      : 'error'
    : 'idle';

  return (
    <Card
      title="SQLite WAL Persistence"
      icon={<HardDrive size={18} />}
      badgeText={badgeText}
      badgeVariant={badgeVariant}
    >
      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: '16px' }}>
        Single-file zero-daemon transactional database with Write-Ahead Logging and Alembic reproducible schema migrations.
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
          label="Database State"
          value={telemetry ? (isConnected ? 'CONNECTED' : 'DISCONNECTED') : null}
        />
        <Metric
          label="Roundtrip Latency"
          value={telemetry?.database_latency_ms ?? null}
          unit="ms"
        />
        <Metric
          label="Journal Mode"
          value={telemetry?.journal_mode?.toUpperCase() ?? null}
        />
        <Metric
          label="SQLite Version"
          value={systemInfo?.sqlite_version ?? null}
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
        <span>Path: com.orbit.app/data/orbit.db</span>
        <span>Alembic: In-Sync</span>
      </div>
    </Card>
  );
};
