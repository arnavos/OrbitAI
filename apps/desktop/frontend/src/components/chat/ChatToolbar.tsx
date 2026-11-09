import React, { useState } from 'react';
import { useChat } from '../../context/ChatContext';
import { useConversations } from '../../hooks/useConversations';
import { ModelSelector } from '../models/ModelSelector';
import { ContextMeter } from './ContextMeter';
import { PanelLeftOpen, Trash2 } from 'lucide-react';
import { ConfirmDialog } from '../ui/ConfirmDialog';

export const ChatToolbar: React.FC = () => {
  const { state, toggleSidebar } = useChat();
  const { activeConversation, deleteConversation } = useConversations();
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  return (
    <>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '8px 16px',
          borderBottom: '1px solid var(--border-subtle)',
          backgroundColor: 'var(--bg-secondary)',
          minHeight: '48px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {!state.sidebarOpen && (
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
              title="Open sidebar (Cmd+B)"
              aria-label="Open sidebar"
            >
              <PanelLeftOpen size={16} />
            </button>
          )}

          <ModelSelector />
          <ContextMeter />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {activeConversation && (
            <button
              onClick={() => setShowDeleteConfirm(true)}
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                padding: '6px',
                borderRadius: 'var(--radius-sm)',
                display: 'flex',
                alignItems: 'center',
              }}
              title="Delete this conversation"
              aria-label="Delete this conversation"
            >
              <Trash2 size={15} />
            </button>
          )}
        </div>
      </div>

      {activeConversation && (
        <ConfirmDialog
          isOpen={showDeleteConfirm}
          title="Delete Conversation"
          message={`Are you sure you want to delete "${activeConversation.title}"?`}
          confirmLabel="Delete"
          isDestructive={true}
          onConfirm={async () => {
            setShowDeleteConfirm(false);
            await deleteConversation(activeConversation.id);
          }}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}
    </>
  );
};
