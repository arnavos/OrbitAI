import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { HostCard } from '../dashboard/HostCard';
import { BackendCard } from '../dashboard/BackendCard';
import { DatabaseCard } from '../dashboard/DatabaseCard';
import { SecurityCard } from '../dashboard/SecurityCard';
import { AuditFeed } from '../dashboard/AuditFeed';
import { StatusBar } from '../layout/StatusBar';
import type { DetailedHealthResponse, SystemInfoResponse, AuditEventResponse } from '../../types/contracts';

const mockSystemInfo: SystemInfoResponse = {
  app_name: 'ORBIT',
  app_version: '0.1.0',
  environment: 'development',
  platform: 'Darwin',
  architecture: 'arm64',
  python_version: '3.13.9',
  sqlite_version: '3.45.1',
  total_memory_mb: 16384,
  available_memory_mb: 8192,
};

const mockTelemetry: DetailedHealthResponse = {
  status: 'healthy',
  version: '0.1.0',
  uptime_seconds: 3665,
  pid: 48210,
  memory_rss_mb: 58.4,
  cpu_percent: 2.3,
  database_connected: true,
  database_latency_ms: 0.42,
  journal_mode: 'wal',
};

const mockAuditEvents: AuditEventResponse[] = [
  {
    id: 1,
    correlation_id: 'corr-12345678-abcd',
    event_type: 'SYSTEM_BOOT',
    actor: 'kernel',
    severity: 'INFO',
    payload: {},
    created_at: '2026-11-01T01:00:00Z',
  },
  {
    id: 2,
    correlation_id: 'corr-87654321-wxyz',
    event_type: 'SETTING_UPDATED',
    actor: 'user',
    severity: 'WARNING',
    payload: { key: 'debug_mode' },
    created_at: '2026-11-01T01:05:00Z',
  },
];

describe('Dashboard Components — Real Measurements & Fallbacks', () => {
  describe('HostCard', () => {
    it('renders actual system info values when available', () => {
      render(<HostCard systemInfo={mockSystemInfo} isDesktop={true} />);
      expect(screen.getByText('Tauri 2 Native Host')).toBeInTheDocument();
      expect(screen.getByText('arm64')).toBeInTheDocument();
      expect(screen.getByText('Darwin')).toBeInTheDocument();
      expect(screen.getByText('16.0 GB')).toBeInTheDocument();
      expect(screen.getByText('8.0 GB')).toBeInTheDocument();
      expect(screen.getByText('NATIVE SHELL')).toBeInTheDocument();
    });

    it('renders explicit UNAVAILABLE states when system info is null', () => {
      render(<HostCard systemInfo={null} isDesktop={false} />);
      expect(screen.getByText('BROWSER DEV')).toBeInTheDocument();
      const unavailables = screen.getAllByText('UNAVAILABLE');
      expect(unavailables.length).toBeGreaterThanOrEqual(2);
    });
  });

  describe('BackendCard', () => {
    it('renders actual process telemetry (PID, RSS, CPU, uptime)', () => {
      render(<BackendCard telemetry={mockTelemetry} systemInfo={mockSystemInfo} />);
      expect(screen.getByText('Python 3.13 Backend')).toBeInTheDocument();
      expect(screen.getByText('48210')).toBeInTheDocument();
      expect(screen.getByText('58.4')).toBeInTheDocument();
      expect(screen.getByText('2.3')).toBeInTheDocument();
      expect(screen.getByText('HEALTHY')).toBeInTheDocument();
      expect(screen.getByText(/Runtime: Python 3.13.9/)).toBeInTheDocument();
    });

    it('renders OFFLINE and explicit UNAVAILABLE states when telemetry is null', () => {
      render(<BackendCard telemetry={null} systemInfo={null} />);
      expect(screen.getByText('OFFLINE')).toBeInTheDocument();
      const unavailables = screen.getAllByText('UNAVAILABLE');
      expect(unavailables.length).toBeGreaterThanOrEqual(4);
    });
  });

  describe('DatabaseCard', () => {
    it('renders actual SQLite telemetry and latency', () => {
      render(<DatabaseCard telemetry={mockTelemetry} systemInfo={mockSystemInfo} />);
      expect(screen.getByText('SQLite WAL Persistence')).toBeInTheDocument();
      expect(screen.getByText('CONNECTED')).toBeInTheDocument();
      expect(screen.getByText('0.42')).toBeInTheDocument();
      expect(screen.getByText('WAL')).toBeInTheDocument();
      expect(screen.getByText('3.45.1')).toBeInTheDocument();
      expect(screen.getByText('WAL ACTIVE')).toBeInTheDocument();
    });

    it('renders OFFLINE and explicit UNAVAILABLE states when disconnected', () => {
      render(<DatabaseCard telemetry={null} systemInfo={null} />);
      expect(screen.getByText('OFFLINE')).toBeInTheDocument();
      const unavailables = screen.getAllByText('UNAVAILABLE');
      expect(unavailables.length).toBeGreaterThanOrEqual(4);
    });
  });

  describe('SecurityCard', () => {
    it('renders dynamic port and hardened state when session token exists', () => {
      render(<SecurityCard backendPort="51234" hasSessionToken={true} />);
      expect(screen.getByText('Security & Sandboxing')).toBeInTheDocument();
      expect(screen.getByText('51234')).toBeInTheDocument();
      expect(screen.getByText('HARDENED')).toBeInTheDocument();
      expect(screen.getByText('Subprotocol Token')).toBeInTheDocument();
    });

    it('renders UNAVAILABLE port and UNSECURED badge when offline', () => {
      render(<SecurityCard backendPort={null} hasSessionToken={false} />);
      expect(screen.getByText('UNSECURED')).toBeInTheDocument();
      const unavailables = screen.getAllByText('UNAVAILABLE');
      expect(unavailables.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe('AuditFeed', () => {
    it('renders stream of audit events when connected', () => {
      render(<AuditFeed events={mockAuditEvents} isConnected={true} />);
      expect(screen.getByText('Audit Event Stream')).toBeInTheDocument();
      expect(screen.getByText('2 EVENTS')).toBeInTheDocument();
      expect(screen.getByText('SYSTEM_BOOT')).toBeInTheDocument();
      expect(screen.getByText('SETTING_UPDATED')).toBeInTheDocument();
    });

    it('renders disconnected state message when backend is offline', () => {
      render(<AuditFeed events={[]} isConnected={false} />);
      expect(screen.getByText('DISCONNECTED')).toBeInTheDocument();
      expect(screen.getByText(/Audit stream unavailable/)).toBeInTheDocument();
    });
  });

  describe('StatusBar', () => {
    it('renders latency and uptime when available', () => {
      render(
        <StatusBar
          connectionStatus="CONNECTED"
          wsLatencyMs={14}
          dbLatencyMs={0.5}
          uptimeSeconds={125}
        />
      );
      expect(screen.getByText('ONLINE')).toBeInTheDocument();
      expect(screen.getByText('14ms')).toBeInTheDocument();
      expect(screen.getByText('0.5ms')).toBeInTheDocument();
      expect(screen.getByText(/UPTIME: 2m 5s/)).toBeInTheDocument();
      expect(screen.getByText('MEMORY BUDGET: 16 GB')).toBeInTheDocument();
      expect(screen.getByText('COST: ₹0/MO')).toBeInTheDocument();
    });

    it('renders UNAVAILABLE for latency metrics when offline', () => {
      render(
        <StatusBar
          connectionStatus="RECONNECTING"
          wsLatencyMs={null}
          dbLatencyMs={null}
          uptimeSeconds={null}
        />
      );
      expect(screen.getByText('RECONNECTING')).toBeInTheDocument();
      const unavailables = screen.getAllByText('UNAVAILABLE');
      expect(unavailables.length).toBe(2); // WS Latency, DB Latency
      expect(screen.getByText('UPTIME: UNAVAILABLE')).toBeInTheDocument();
    });
  });
});
