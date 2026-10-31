import React from 'react';
import { Badge, type BadgeVariant } from './Badge';

export interface CardProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'title'> {
  title?: React.ReactNode;
  icon?: React.ReactNode;
  badgeText?: string;
  badgeVariant?: BadgeVariant;
  children: React.ReactNode;
  className?: string;
  glow?: boolean;
}

export const Card: React.FC<CardProps> = ({
  title,
  icon,
  badgeText,
  badgeVariant = 'info',
  children,
  className = '',
  glow = false,
  style,
  ...props
}) => {
  return (
    <div
      className={`glass-panel ${className}`}
      style={{
        padding: '20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
        boxShadow: glow ? '0 0 20px -5px var(--accent-glow)' : 'var(--shadow-card)',
        transition: 'all 0.2s ease-in-out',
        ...style,
      }}
      {...props}
    >
      {title && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {icon && <span style={{ display: 'flex', alignItems: 'center', color: 'var(--accent-primary)' }}>{icon}</span>}
            <h2 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
              {title}
            </h2>
          </div>
          {badgeText && (
            <Badge label={badgeText} variant={badgeVariant} />
          )}
        </div>
      )}
      {children}
    </div>
  );
};
