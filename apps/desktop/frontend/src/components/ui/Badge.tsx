import React from 'react';

export type BadgeVariant = 'healthy' | 'warning' | 'error' | 'info' | 'idle';

export interface BadgeProps {
  label: string;
  variant?: BadgeVariant;
  pulse?: boolean;
}

export const Badge: React.FC<BadgeProps> = ({ label, variant = 'info', pulse = false }) => {
  const getColors = () => {
    switch (variant) {
      case 'healthy':
        return {
          bg: 'rgba(16, 185, 129, 0.1)',
          color: '#10b981',
          border: 'rgba(16, 185, 129, 0.25)',
          dot: '#10b981',
        };
      case 'warning':
        return {
          bg: 'rgba(245, 158, 11, 0.1)',
          color: '#f59e0b',
          border: 'rgba(245, 158, 11, 0.25)',
          dot: '#f59e0b',
        };
      case 'error':
        return {
          bg: 'rgba(239, 68, 68, 0.1)',
          color: '#ef4444',
          border: 'rgba(239, 68, 68, 0.25)',
          dot: '#ef4444',
        };
      case 'info':
        return {
          bg: 'rgba(99, 102, 241, 0.1)',
          color: 'var(--accent-primary)',
          border: 'var(--border-accent)',
          dot: 'var(--accent-primary)',
        };
      case 'idle':
      default:
        return {
          bg: 'rgba(100, 116, 139, 0.1)',
          color: 'var(--text-muted)',
          border: 'var(--border-subtle)',
          dot: '#64748b',
        };
    }
  };

  const colors = getColors();

  return (
    <div
      className="status-badge"
      style={{
        background: colors.bg,
        color: colors.color,
        border: `1px solid ${colors.border}`,
      }}
    >
      <span
        className={`status-dot ${pulse ? 'pulse-subtle' : ''}`}
        style={{ backgroundColor: colors.dot }}
      />
      <span>{label}</span>
    </div>
  );
};
