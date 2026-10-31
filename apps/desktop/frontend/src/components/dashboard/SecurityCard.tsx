import React from 'react';
import { ShieldCheck } from 'lucide-react';
import { Card } from '../ui/Card';
import { Metric } from '../ui/Metric';

export interface SecurityCardProps {
  backendPort: string | null;
  hasSessionToken: boolean;
}

export const SecurityCard: React.FC<SecurityCardProps> = ({ backendPort, hasSessionToken }) => {
  return (
    <Card
      title="Security & Sandboxing"
      icon={<ShieldCheck size={18} />}
      badgeText={hasSessionToken ? 'HARDENED' : 'UNSECURED'}
      badgeVariant={hasSessionToken ? 'healthy' : 'warning'}
    >
      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: '16px' }}>
        Strict Content Security Policy (CSP), origin validation, process watchdog, and zero blind execution gates.
      </p>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: '12px',
          marginBottom: '16px',
        }}
      >
        <Metric label="Loopback Binding" value="127.0.0.1" />
        <Metric label="Dynamic Port" value={backendPort} />
        <Metric
          label="WS Authentication"
          value={hasSessionToken ? 'Subprotocol Token' : null}
        />
        <Metric label="CSP Enforcement" value="script-src 'self'" />
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
        <span>Token Storage: In-Memory / Closure</span>
        <span>Origin: tauri://localhost</span>
      </div>
    </Card>
  );
};
