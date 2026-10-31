import React from 'react';

export interface MetricProps {
  label: string;
  value: string | number | null | undefined;
  unit?: string;
  subtext?: string;
  status?: 'healthy' | 'warning' | 'error' | 'neutral';
}

export const Metric: React.FC<MetricProps> = ({ label, value, unit, subtext, status = 'neutral' }) => {
  const isAvailable = value !== null && value !== undefined && value !== '';

  const getStatusColor = () => {
    switch (status) {
      case 'healthy':
        return '#10b981';
      case 'warning':
        return '#f59e0b';
      case 'error':
        return '#ef4444';
      case 'neutral':
      default:
        return 'var(--text-primary)';
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
      <span style={{ fontSize: '11px', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {label}
      </span>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '4px' }}>
        {isAvailable ? (
          <>
            <span
              style={{
                fontSize: '18px',
                fontWeight: 600,
                fontFamily: 'var(--font-mono)',
                color: getStatusColor(),
              }}
            >
              {value}
            </span>
            {unit && (
              <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                {unit}
              </span>
            )}
          </>
        ) : (
          <span
            style={{
              fontSize: '12px',
              fontWeight: 500,
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)',
              fontStyle: 'italic',
            }}
          >
            UNAVAILABLE
          </span>
        )}
      </div>
      {subtext && (
        <span style={{ fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          {subtext}
        </span>
      )}
    </div>
  );
};
