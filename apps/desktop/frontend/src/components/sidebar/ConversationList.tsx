import React from 'react';
import { useConversations } from '../../hooks/useConversations';
import { ConversationItem } from './ConversationItem';

export const ConversationList: React.FC = () => {
  const {
    filteredConversations,
    activeConversationId,
    setActiveConversationId,
    updateConversationTitle,
    deleteConversation,
    isLoading,
  } = useConversations();

  if (isLoading && filteredConversations.length === 0) {
    return (
      <div style={{ padding: '16px', textAlign: 'center', fontSize: '12px', color: 'var(--text-muted)' }}>
        Loading conversations...
      </div>
    );
  }

  if (filteredConversations.length === 0) {
    return (
      <div
        style={{
          padding: '24px 16px',
          textAlign: 'center',
          fontSize: '12px',
          color: 'var(--text-muted)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '6px',
        }}
      >
        <span>No conversations found</span>
        <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
          Start a new chat to begin.
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '2px',
        overflowY: 'auto',
        flex: 1,
        paddingRight: '4px',
      }}
    >
      {filteredConversations.map((conv) => (
        <ConversationItem
          key={conv.id}
          conversation={conv}
          isActive={conv.id === activeConversationId}
          onSelect={() => setActiveConversationId(conv.id)}
          onRename={(newTitle) => updateConversationTitle(conv.id, newTitle)}
          onDelete={() => deleteConversation(conv.id)}
        />
      ))}
    </div>
  );
};
