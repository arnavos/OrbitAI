import React from 'react';
import { useChat } from '../../context/ChatContext';
import { useConversations } from '../../hooks/useConversations';
import { useTheme } from '../../context/ThemeContext';
import { ConversationList } from './ConversationList';
import { Plus, Search, PanelLeftClose, Activity, Sun, Moon } from 'lucide-react';

export const Sidebar: React.FC = () => {
  const { state, toggleSidebar, setActiveView } = useChat();
  const { searchQuery, setSearchQuery, createConversation } = useConversations();
  const { theme, toggleTheme } = useTheme();

  if (!state.sidebarOpen) {
    return null;
  }

  const handleNewChat = async () => {
    await createConversation('New Conversation');
    if (state.activeView !== 'chat') {
      setActiveView('chat');
    }
  };

  return (
    <aside
      style={{
        width: '260px',
        backgroundColor: 'var(--bg-secondary)',
        borderRight: '1px solid var(--border-subtle)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        padding: '12px',
        gap: '12px',
        flexShrink: 0,
      }}
    >
      {/* Top Header: Branding & Collapse */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '2px 4px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ color: 'var(--accent-primary)', fontSize: '18px', fontWeight: 700 }}>
            ⬡
          </span>
          <span style={{ fontSize: '14px', fontWeight: 600, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>
            Orbit AI
          </span>
          <span
            style={{
              fontSize: '10px',
              padding: '1px 5px',
              backgroundColor: 'var(--bg-tertiary)',
              borderRadius: '4px',
              color: 'var(--text-muted)',
              fontFamily: 'var(--font-mono)',
            }}
          >
            v0.1.0
          </span>
        </div>

        <button
          onClick={toggleSidebar}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            padding: '4px',
            borderRadius: 'var(--radius-sm)',
            display: 'flex',
            alignItems: 'center',
          }}
          title="Collapse sidebar (Cmd+B)"
          aria-label="Collapse sidebar"
        >
          <PanelLeftClose size={16} />
        </button>
      </div>

      {/* New Chat Button */}
      <button
        onClick={handleNewChat}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 12px',
          backgroundColor: 'var(--bg-tertiary)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-md)',
          color: 'var(--text-primary)',
          fontSize: '13px',
          fontWeight: 500,
          cursor: 'pointer',
          transition: 'all 0.15s ease-in-out',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Plus size={15} style={{ color: 'var(--accent-primary)' }} />
          <span>New Chat</span>
        </div>
        <kbd
          style={{
            fontSize: '10px',
            fontFamily: 'var(--font-mono)',
            backgroundColor: 'var(--bg-secondary)',
            padding: '2px 5px',
            borderRadius: '3px',
            color: 'var(--text-muted)',
            border: '1px solid var(--border-subtle)',
          }}
        >
          ⌘N
        </kbd>
      </button>

      {/* Search Input */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '6px 10px',
          backgroundColor: 'var(--bg-primary)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-sm)',
        }}
      >
        <Search size={13} style={{ color: 'var(--text-muted)' }} />
        <input
          type="text"
          placeholder="Search conversations..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-primary)',
            fontSize: '12px',
            outline: 'none',
            width: '100%',
            fontFamily: 'inherit',
          }}
        />
      </div>

      {/* Conversation Thread List */}
      <ConversationList />

      {/* Bottom Footer Actions */}
      <div
        style={{
          borderTop: '1px solid var(--border-subtle)',
          paddingTop: '10px',
          display: 'flex',
          flexDirection: 'column',
          gap: '4px',
        }}
      >
        <button
          onClick={() => setActiveView(state.activeView === 'chat' ? 'system' : 'chat')}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '7px 10px',
            backgroundColor: state.activeView === 'system' ? 'var(--bg-tertiary)' : 'transparent',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            color: state.activeView === 'system' ? 'var(--accent-primary)' : 'var(--text-secondary)',
            fontSize: '12px',
            fontWeight: 500,
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Activity size={14} />
            <span>{state.activeView === 'system' ? 'Return to Chat' : 'System Diagnostics'}</span>
          </div>
          <span style={{ fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            M0
          </span>
        </button>

        <button
          onClick={toggleTheme}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '7px 10px',
            backgroundColor: 'transparent',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--text-secondary)',
            fontSize: '12px',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
            <span>{theme === 'dark' ? 'Light Mode' : 'Dark Mode'}</span>
          </div>
        </button>
      </div>
    </aside>
  );
};
