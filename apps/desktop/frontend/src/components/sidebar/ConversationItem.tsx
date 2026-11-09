import React, { useState, useRef, useEffect } from 'react';
import type { ConversationResponse } from '../../types/conversation';
import { MessageSquare, Edit3, Trash2, Check, X } from 'lucide-react';
import { ConfirmDialog } from '../ui/ConfirmDialog';

export interface ConversationItemProps {
  conversation: ConversationResponse;
  isActive: boolean;
  onSelect: () => void;
  onRename: (newTitle: string) => Promise<void>;
  onDelete: () => Promise<void>;
}

export const ConversationItem: React.FC<ConversationItemProps> = ({
  conversation,
  isActive,
  onSelect,
  onRename,
  onDelete,
}) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(conversation.title);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  const handleSaveRename = async () => {
    if (editTitle.trim() && editTitle !== conversation.title) {
      await onRename(editTitle.trim());
    }
    setIsEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSaveRename();
    } else if (e.key === 'Escape') {
      setEditTitle(conversation.title);
      setIsEditing(false);
    }
  };

  return (
    <>
      <div
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        onClick={() => !isEditing && onSelect()}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 10px',
          borderRadius: 'var(--radius-sm)',
          backgroundColor: isActive
            ? 'var(--bg-tertiary)'
            : isHovered
            ? 'var(--bg-card-hover)'
            : 'transparent',
          color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
          cursor: isEditing ? 'default' : 'pointer',
          borderLeft: isActive ? '3px solid var(--accent-primary)' : '3px solid transparent',
          transition: 'all 0.15s ease-in-out',
          position: 'relative',
        }}
        role="button"
        tabIndex={0}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', overflow: 'hidden', flex: 1 }}>
          <MessageSquare size={14} style={{ flexShrink: 0, color: isActive ? 'var(--accent-primary)' : 'var(--text-muted)' }} />

          {isEditing ? (
            <input
              ref={inputRef}
              type="text"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onBlur={handleSaveRename}
              onKeyDown={handleKeyDown}
              style={{
                width: '100%',
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--accent-primary)',
                borderRadius: 'var(--radius-sm)',
                padding: '2px 6px',
                fontSize: '13px',
                color: 'var(--text-primary)',
                fontFamily: 'inherit',
                outline: 'none',
              }}
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <span
              style={{
                fontSize: '13px',
                fontWeight: isActive ? 500 : 400,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
              title={conversation.title}
            >
              {conversation.title}
            </span>
          )}
        </div>

        {/* Action icons on hover or editing */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginLeft: '6px' }} onClick={(e) => e.stopPropagation()}>
          {isEditing ? (
            <>
              <button
                onClick={handleSaveRename}
                style={{ background: 'transparent', border: 'none', color: 'var(--status-healthy)', cursor: 'pointer', padding: '2px' }}
                title="Save title"
              >
                <Check size={13} />
              </button>
              <button
                onClick={() => {
                  setEditTitle(conversation.title);
                  setIsEditing(false);
                }}
                style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '2px' }}
                title="Cancel rename"
              >
                <X size={13} />
              </button>
            </>
          ) : (
            (isHovered || isActive) && (
              <>
                <button
                  onClick={() => setIsEditing(true)}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--text-muted)',
                    cursor: 'pointer',
                    padding: '2px',
                    borderRadius: '2px',
                  }}
                  title="Rename conversation"
                  aria-label="Rename conversation"
                >
                  <Edit3 size={13} />
                </button>
                <button
                  onClick={() => setShowDeleteConfirm(true)}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--text-muted)',
                    cursor: 'pointer',
                    padding: '2px',
                    borderRadius: '2px',
                  }}
                  title="Delete conversation"
                  aria-label="Delete conversation"
                >
                  <Trash2 size={13} />
                </button>
              </>
            )
          )}
        </div>
      </div>

      <ConfirmDialog
        isOpen={showDeleteConfirm}
        title="Delete Conversation"
        message={`Are you sure you want to permanently delete "${conversation.title}"? This action cannot be undone.`}
        confirmLabel="Delete"
        isDestructive={true}
        onConfirm={async () => {
          setShowDeleteConfirm(false);
          await onDelete();
        }}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </>
  );
};
