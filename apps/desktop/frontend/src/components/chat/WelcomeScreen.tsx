import React from 'react';
import { useModels } from '../../hooks/useModels';
import { Sparkles, Shield, Cpu, Terminal } from 'lucide-react';

export interface WelcomeScreenProps {
  onSelectPrompt: (prompt: string) => void;
}

export const WelcomeScreen: React.FC<WelcomeScreenProps> = ({ onSelectPrompt }) => {
  const { selectedModel } = useModels();

  const starterPrompts = [
    {
      title: 'Analyze Architecture',
      prompt: 'Explain the benefits of SQLite Write-Ahead Logging (WAL) mode for local desktop applications.',
      icon: <Cpu size={16} />,
    },
    {
      title: 'Debug Python Asyncio',
      prompt: 'How do you shield database writes from task cancellation in Python asyncio?',
      icon: <Terminal size={16} />,
    },
    {
      title: 'Code Review',
      prompt: 'Review this TypeScript function for race conditions and memory leaks.',
      icon: <Sparkles size={16} />,
    },
  ];

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        padding: '32px 16px',
        maxWidth: '680px',
        margin: '0 auto',
        textAlign: 'center',
        gap: '24px',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
        <div
          style={{
            width: '48px',
            height: '48px',
            borderRadius: '12px',
            backgroundColor: 'var(--bg-tertiary)',
            border: '1px solid var(--border-accent)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-primary)',
            fontSize: '24px',
            fontWeight: 700,
            boxShadow: '0 0 25px -5px var(--accent-glow)',
          }}
        >
          ⬡
        </div>
        <h2 style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)', marginTop: '4px' }}>
          Welcome to ORBIT
        </h2>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', maxWidth: '440px', lineHeight: 1.5 }}>
          Your Autonomous Personal Intelligence Runtime. 100% private, local-first inference running on Apple Silicon.
        </p>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            marginTop: '4px',
            fontSize: '11px',
            color: 'var(--status-healthy)',
            backgroundColor: 'rgba(16, 185, 129, 0.1)',
            padding: '3px 10px',
            borderRadius: '9999px',
            fontFamily: 'var(--font-mono)',
          }}
        >
          <Shield size={12} />
          <span>Local Device Only • ₹0/mo Cloud Cost</span>
          {selectedModel && <span>• {selectedModel.name || selectedModel.id.replace('ollama/', '')}</span>}
        </div>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '12px',
          width: '100%',
          marginTop: '8px',
        }}
      >
        {starterPrompts.map((starter, index) => (
          <button
            key={index}
            onClick={() => onSelectPrompt(starter.prompt)}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-start',
              padding: '14px',
              backgroundColor: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--text-primary)',
              cursor: 'pointer',
              textAlign: 'left',
              gap: '6px',
              transition: 'all 0.15s ease-in-out',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--accent-primary)', fontSize: '12px', fontWeight: 600 }}>
              {starter.icon}
              <span>{starter.title}</span>
            </div>
            <p
              style={{
                fontSize: '11px',
                color: 'var(--text-secondary)',
                lineHeight: 1.4,
                margin: 0,
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {starter.prompt}
            </p>
          </button>
        ))}
      </div>
    </div>
  );
};
