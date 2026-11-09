import React from 'react';
import { useChat } from '../../context/ChatContext';
import { useModels } from '../../hooks/useModels';
import { Layers } from 'lucide-react';

export const ContextMeter: React.FC = () => {
  const { state } = useChat();
  const { selectedModel } = useModels();

  const allocatedContext = state.streamingState.allocatedContext;
  const capacity = selectedModel?.context_window_capacity || 8192;
  const tokenCount = state.streamingState.tokenCount;

  const displayContext = allocatedContext || capacity;
  const formattedCapacity =
    displayContext >= 1024
      ? `${Math.round(displayContext / 1024)}k`
      : `${displayContext}`;

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        padding: '4px 8px',
        borderRadius: 'var(--radius-sm)',
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        fontSize: '11px',
        fontFamily: 'var(--font-mono)',
        color: 'var(--text-secondary)',
      }}
      title={`Context window: ${displayContext.toLocaleString()} tokens`}
    >
      <Layers size={13} style={{ color: 'var(--text-muted)' }} />
      <span>{formattedCapacity} context</span>
      {tokenCount > 0 && (
        <span style={{ color: 'var(--accent-primary)', fontWeight: 600 }}>
          ({tokenCount} gen)
        </span>
      )}
    </div>
  );
};
