import React from 'react';
import { useChat } from '../../context/ChatContext';
import { MessageSquare, Activity } from 'lucide-react';
import type { ConnectionStatus } from '../../types/contracts';

export interface TitleBarProps {
  connectionStatus?: ConnectionStatus;
  port?: string | null;
}

export const TitleBar: React.FC<TitleBarProps> = ({ connectionStatus, port }) => {
  const { state, setActiveView } = useChat();

  return (
    <header
      data-tauri-drag-region
      style={{
        height: '38px',
        backgroundColor: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border-subtle)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        paddingLeft: '78px', // Reserves spacing for macOS window traffic lights
        paddingRight: '12px',
        userSelect: 'none',
        flexShrink: 0,
        zIndex: 50,
      }}
    >
      {/* Center view toggle tabs */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
        <button
          onClick={() => setActiveView('chat')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '3px 10px',
            borderRadius: 'var(--radius-sm)',
            border: 'none',
            backgroundColor: state.activeView === 'chat' ? 'var(--bg-tertiary)' : 'transparent',
            color: state.activeView === 'chat' ? 'var(--text-primary)' : 'var(--text-muted)',
            fontSize: '12px',
            fontWeight: 500,
            cursor: 'pointer',
            transition: 'all 0.15s ease',
          }}
        >
          <MessageSquare size={13} style={{ color: state.activeView === 'chat' ? 'var(--accent-primary)' : 'inherit' }} />
          <span>Chat</span>
        </button>

        <button
          onClick={() => setActiveView('system')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '3px 10px',
            borderRadius: 'var(--radius-sm)',
            border: 'none',
            backgroundColor: state.activeView === 'system' ? 'var(--bg-tertiary)' : 'transparent',
            color: state.activeView === 'system' ? 'var(--text-primary)' : 'var(--text-muted)',
            fontSize: '12px',
            fontWeight: 500,
            cursor: 'pointer',
            transition: 'all 0.15s ease',
          }}
        >
          <Activity size={13} style={{ color: state.activeView === 'system' ? 'var(--accent-primary)' : 'inherit' }} />
          <span>System</span>
        </button>
      </div>

      {/* Right status badge */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '11px',
            fontFamily: 'var(--font-mono)',
            color: 'var(--text-muted)',
          }}
        >
          <span
            className="status-dot"
            style={{
              backgroundColor: connectionStatus === 'CONNECTED' ? 'var(--status-healthy)' : 'var(--accent-primary)',
            }}
          />
          <span>{connectionStatus || 'STANDALONE DEV'}</span>
          {port && <span>:{port}</span>}
        </div>
      </div>
    </header>
  );
};
