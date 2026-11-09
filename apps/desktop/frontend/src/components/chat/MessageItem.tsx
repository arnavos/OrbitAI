import React, { useState } from 'react';
import type { MessageResponse } from '../../types/conversation';
import { MarkdownRenderer } from '../markdown/MarkdownRenderer';
import { Copy, Check, AlertCircle, StopCircle } from 'lucide-react';

export interface MessageItemProps {
  message: MessageResponse;
}

export const MessageItem: React.FC<MessageItemProps> = React.memo(({ message }) => {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === 'user';

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy message:', err);
    }
  };

  const formatTimestamp = (isoString?: string) => {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  };

  const isInterrupted = message.status === 'interrupted';
  const isFailed = message.status === 'failed';

  if (isUser) {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-end',
          width: '100%',
          margin: '10px 0',
        }}
      >
        <div
          style={{
            fontSize: '11px',
            color: 'var(--text-muted)',
            marginBottom: '4px',
            marginRight: '6px',
            fontFamily: 'var(--font-mono)',
          }}
        >
          {formatTimestamp(message.created_at)}
        </div>
        <div
          style={{
            padding: '12px 16px',
            backgroundColor: 'var(--bg-user-bubble)',
            border: '1px solid var(--border-accent)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)',
            maxWidth: '75%',
            fontSize: '14px',
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {message.content}
        </div>
      </div>
    );
  }

  // Assistant Message
  const modelLabel = message.model_id ? message.model_id.replace('ollama/', '') : 'Assistant';

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-start',
        width: '100%',
        margin: '12px 0',
      }}
    >
      {/* Header bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          marginBottom: '6px',
          fontSize: '11px',
          color: 'var(--text-muted)',
          fontFamily: 'var(--font-mono)',
        }}
      >
        <span style={{ color: 'var(--accent-primary)', fontWeight: 700 }}>⬡</span>
        <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{modelLabel}</span>
        <span>{formatTimestamp(message.created_at)}</span>

        {isInterrupted && (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              color: 'var(--status-warning)',
              backgroundColor: 'rgba(245, 158, 11, 0.1)',
              padding: '1px 6px',
              borderRadius: '4px',
            }}
          >
            <StopCircle size={11} />
            <span>Interrupted</span>
          </span>
        )}

        {isFailed && (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              color: 'var(--status-error)',
              backgroundColor: 'rgba(239, 68, 68, 0.1)',
              padding: '1px 6px',
              borderRadius: '4px',
            }}
          >
            <AlertCircle size={11} />
            <span>Failed</span>
          </span>
        )}
      </div>

      {/* Message Content Container */}
      <div
        style={{
          padding: '14px 18px',
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-md)',
          maxWidth: '85%',
          width: '100%',
          boxShadow: 'var(--shadow-card)',
          position: 'relative',
        }}
      >
        <MarkdownRenderer content={message.content} />

        {isFailed && message.error_message && (
          <div
            style={{
              marginTop: '10px',
              padding: '8px 12px',
              backgroundColor: 'rgba(239, 68, 68, 0.1)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--status-error)',
              fontSize: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <AlertCircle size={14} />
            <span>{message.error_message}</span>
          </div>
        )}

        {/* Footer actions & metrics */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginTop: '12px',
            paddingTop: '8px',
            borderTop: '1px solid var(--border-subtle)',
            fontSize: '11px',
            fontFamily: 'var(--font-mono)',
            color: 'var(--text-muted)',
          }}
        >
          <div style={{ display: 'flex', gap: '8px' }}>
            {message.completion_tokens !== null && message.completion_tokens !== undefined && (
              <span>{message.completion_tokens} tokens</span>
            )}
            {message.finish_reason && <span>• reason: {message.finish_reason}</span>}
          </div>

          <button
            onClick={handleCopy}
            aria-label={copied ? 'Response copied' : 'Copy response to clipboard'}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              background: 'transparent',
              border: 'none',
              color: copied ? 'var(--status-healthy)' : 'var(--text-muted)',
              cursor: 'pointer',
              fontSize: '11px',
              fontFamily: 'var(--font-mono)',
              padding: '2px 4px',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
        </div>
      </div>
    </div>
  );
});
