import React from 'react';
import { MarkdownRenderer } from '../markdown/MarkdownRenderer';

export interface StreamingMessageProps {
  content: string;
  tokenCount: number;
  modelId?: string | null;
}

export const StreamingMessage: React.FC<StreamingMessageProps> = ({
  content,
  tokenCount,
  modelId,
}) => {
  const modelName = modelId ? modelId.replace('ollama/', '') : 'Local Model';

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-start',
        width: '100%',
        margin: '12px 0',
        animation: 'fadeIn 0.2s ease-in',
      }}
    >
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
        <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{modelName}</span>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            color: 'var(--accent-primary)',
          }}
        >
          <span className="status-dot" style={{ backgroundColor: 'var(--accent-primary)' }} />
          <span>Generating...</span>
        </span>
        {tokenCount > 0 && <span>({tokenCount} tokens)</span>}
      </div>

      <div
        style={{
          padding: '14px 18px',
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--border-accent)',
          borderRadius: 'var(--radius-md)',
          maxWidth: '85%',
          width: '100%',
          boxShadow: 'var(--shadow-card)',
        }}
      >
        {content ? (
          <div>
            <MarkdownRenderer content={content} />
            <span className="cursor-pulse" />
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', fontSize: '13px' }}>
            <span className="cursor-pulse" />
            <span>Thinking...</span>
          </div>
        )}
      </div>
    </div>
  );
};
