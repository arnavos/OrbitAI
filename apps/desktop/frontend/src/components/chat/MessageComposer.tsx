import React, { useRef, useEffect } from 'react';
import { useStreamingChat } from '../../hooks/useStreamingChat';
import { ArrowUp, Square, AlertCircle, X } from 'lucide-react';

export interface MessageComposerProps {
  onSendMessage?: (content: string) => void;
}

export const MessageComposer: React.FC<MessageComposerProps> = ({ onSendMessage }) => {
  const {
    draft,
    updateDraft,
    sendMessage,
    cancelStreaming,
    isStreaming,
    isSubmitting,
    error,
    clearError,
  } = useStreamingChat();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea height
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      const scrollHeight = textareaRef.current.scrollHeight;
      const newHeight = Math.min(Math.max(scrollHeight, 44), 180);
      textareaRef.current.style.height = `${newHeight}px`;
    }
  }, [draft]);

  const estimateTokens = (text: string): number => {
    if (!text.trim()) return 0;
    return Math.ceil(text.length / 3.5) + 4;
  };

  const tokenEstimate = estimateTokens(draft);
  const isSendDisabled = !draft.trim() || isStreaming || Boolean(isSubmitting);

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (isSendDisabled) return;
    const textToSend = draft;
    if (onSendMessage) {
      onSendMessage(textToSend);
    } else {
      sendMessage(textToSend);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div
      style={{
        padding: '12px 24px 18px',
        maxWidth: '820px',
        width: '100%',
        margin: '0 auto',
      }}
    >
      {error && (
        <div
          role="alert"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '8px 12px',
            backgroundColor: 'rgba(239, 68, 68, 0.12)',
            border: '1px solid var(--status-error)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--status-error)',
            fontSize: '12px',
            marginBottom: '8px',
            gap: '8px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <AlertCircle size={15} style={{ flexShrink: 0 }} />
            <span>{error}</span>
          </div>
          <button
            type="button"
            onClick={clearError}
            aria-label="Dismiss error"
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--status-error)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              padding: '2px',
            }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        style={{
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-card)',
          padding: '10px 14px',
          gap: '8px',
          transition: 'border-color 0.15s ease',
        }}
      >
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => updateDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask ORBIT anything... (Shift+Enter for newline)"
          rows={1}
          disabled={isStreaming || Boolean(isSubmitting)}
          style={{
            width: '100%',
            backgroundColor: 'transparent',
            border: 'none',
            outline: 'none',
            color: 'var(--text-primary)',
            fontSize: '14px',
            lineHeight: 1.5,
            resize: 'none',
            fontFamily: 'inherit',
            maxHeight: '180px',
            overflowY: 'auto',
          }}
        />

        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            paddingTop: '4px',
          }}
        >
          <div
            style={{
              fontSize: '11px',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)',
            }}
          >
            {tokenEstimate > 0 ? (
              <span>~{tokenEstimate} est. tokens</span>
            ) : (
              <span>Shift+Enter for newline</span>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {isStreaming ? (
              <button
                type="button"
                onClick={cancelStreaming}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  backgroundColor: 'var(--status-error)',
                  color: '#ffffff',
                  border: 'none',
                  borderRadius: 'var(--radius-sm)',
                  padding: '5px 10px',
                  fontSize: '12px',
                  fontWeight: 500,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
                aria-label="Stop generation"
              >
                <Square size={12} fill="#ffffff" />
                <span>Stop</span>
              </button>
            ) : (
              <button
                type="submit"
                disabled={isSendDisabled}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: '30px',
                  height: '30px',
                  borderRadius: '50%',
                  backgroundColor: !isSendDisabled ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                  color: !isSendDisabled ? '#ffffff' : 'var(--text-muted)',
                  border: 'none',
                  cursor: !isSendDisabled ? 'pointer' : 'not-allowed',
                  transition: 'all 0.15s ease',
                }}
                aria-label="Send message"
              >
                <ArrowUp size={16} />
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
};

