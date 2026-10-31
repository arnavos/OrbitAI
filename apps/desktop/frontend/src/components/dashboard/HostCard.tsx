import React from 'react';
import { Terminal } from 'lucide-react';
import { Card } from '../ui/Card';
import { Metric } from '../ui/Metric';
import type { SystemInfoResponse } from '../../types/contracts';

export interface HostCardProps {
  systemInfo: SystemInfoResponse | null;
  isDesktop: boolean;
}

export const HostCard: React.FC<HostCardProps> = ({ systemInfo, isDesktop }) => {
  const totalMem = systemInfo?.total_memory_mb
    ? `${(systemInfo.total_memory_mb / 1024).toFixed(1)} GB`
    : null;
  const availMem = systemInfo?.available_memory_mb
    ? `${(systemInfo.available_memory_mb / 1024).toFixed(1)} GB`
    : null;

  return (
    <Card
      title="Tauri 2 Native Host"
      icon={<Terminal size={18} />}
      badgeText={isDesktop ? 'NATIVE SHELL' : 'BROWSER DEV'}
      badgeVariant={isDesktop ? 'healthy' : 'info'}
    >
      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: '16px' }}>
        Rust-native desktop container managing windowing, WKWebView, and the Python backend child process lifecycle.
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
          label="Architecture"
          value={systemInfo?.architecture ?? (isDesktop ? 'aarch64' : null)}
        />
        <Metric
          label="Platform / OS"
          value={systemInfo?.platform ?? (isDesktop ? 'Darwin' : null)}
        />
        <Metric label="Host Memory" value={totalMem} />
        <Metric label="Available Memory" value={availMem} />
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
        <span>Environment: {systemInfo?.environment ?? 'UNAVAILABLE'}</span>
        <span>App: {systemInfo?.app_version ? `v${systemInfo.app_version}` : 'UNAVAILABLE'}</span>
      </div>
    </Card>
  );
};
