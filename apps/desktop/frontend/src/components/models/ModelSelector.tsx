import React, { useState, useRef, useEffect } from 'react';
import { useModels } from '../../hooks/useModels';
import { ChevronDown, Sparkles, Check, AlertCircle } from 'lucide-react';

export const ModelSelector: React.FC = () => {
  const { models, selectedModel, selectedModelId, setSelectedModelId, isLoading, error } =
    useModels();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleOutsideClick);
    }
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick);
    };
  }, [isOpen]);

  const formatContextSize = (tokens?: number | null) => {
    if (!tokens) return '';
    if (tokens >= 1024) {
      return `${Math.round(tokens / 1024)}k ctx`;
    }
    return `${tokens} ctx`;
  };

  const displayName = selectedModel
    ? selectedModel.name || selectedModel.id.replace('ollama/', '')
    : selectedModelId.replace('ollama/', '');

  return (
    <div ref={containerRef} style={{ position: 'relative' }}>
      <button
        onClick={() => setIsOpen((prev) => !prev)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '6px 10px',
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-md)',
          color: 'var(--text-primary)',
          fontSize: '13px',
          fontWeight: 500,
          cursor: 'pointer',
          transition: 'all 0.15s ease-in-out',
        }}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
      >
        <Sparkles size={14} style={{ color: 'var(--accent-primary)' }} />
        <span>{displayName}</span>
        {selectedModel?.parameter_size && (
          <span
            style={{
              fontSize: '11px',
              fontFamily: 'var(--font-mono)',
              padding: '2px 5px',
              backgroundColor: 'var(--bg-tertiary)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-secondary)',
            }}
          >
            {selectedModel.parameter_size}
          </span>
        )}
        <ChevronDown size={14} style={{ color: 'var(--text-muted)' }} />
      </button>

      {isOpen && (
        <div
          className="glass-panel"
          style={{
            position: 'absolute',
            top: 'calc(100% + 6px)',
            left: 0,
            width: '280px',
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: 'var(--radius-md)',
            boxShadow: 'var(--shadow-card)',
            padding: '6px',
            zIndex: 1000,
            display: 'flex',
            flexDirection: 'column',
            gap: '2px',
          }}
          role="listbox"
        >
          <div
            style={{
              padding: '6px 10px',
              fontSize: '11px',
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              color: 'var(--text-muted)',
              borderBottom: '1px solid var(--border-subtle)',
              marginBottom: '4px',
            }}
          >
            Installed Local Models
          </div>

          {isLoading ? (
            <div style={{ padding: '12px', textAlign: 'center', fontSize: '12px', color: 'var(--text-muted)' }}>
              Discovering local models...
            </div>
          ) : models.length === 0 ? (
            <div style={{ padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--status-warning)', fontSize: '12px' }}>
                <AlertCircle size={14} />
                <span>No local models found</span>
              </div>
              <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                {error ? error : 'Run `ollama pull llama3.2:3b` in Terminal to install a local model.'}
              </p>
            </div>
          ) : (
            models.map((m) => {
              const isSelected = m.id === selectedModelId;
              const isLoaded = m.availability_status === 'loaded';
              return (
                <button
                  key={m.id}
                  onClick={() => {
                    setSelectedModelId(m.id);
                    setIsOpen(false);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 10px',
                    borderRadius: 'var(--radius-sm)',
                    border: 'none',
                    backgroundColor: isSelected ? 'var(--bg-tertiary)' : 'transparent',
                    color: isSelected ? 'var(--text-primary)' : 'var(--text-secondary)',
                    cursor: 'pointer',
                    textAlign: 'left',
                    width: '100%',
                    transition: 'background-color 0.1s',
                  }}
                  role="option"
                  aria-selected={isSelected}
                >
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span style={{ fontSize: '13px', fontWeight: isSelected ? 600 : 400, color: 'var(--text-primary)' }}>
                        {m.name || m.id.replace('ollama/', '')}
                      </span>
                      {isLoaded && (
                        <span
                          style={{
                            fontSize: '9px',
                            padding: '1px 4px',
                            borderRadius: '3px',
                            backgroundColor: 'rgba(16, 185, 129, 0.15)',
                            color: 'var(--status-healthy)',
                            fontWeight: 600,
                          }}
                        >
                          RAM
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'flex', gap: '6px' }}>
                      {m.parameter_size && <span>{m.parameter_size}</span>}
                      {m.quantization && <span>• {m.quantization}</span>}
                      {m.context_window_capacity && (
                        <span>• {formatContextSize(m.context_window_capacity)}</span>
                      )}
                    </div>
                  </div>
                  {isSelected && <Check size={14} style={{ color: 'var(--accent-primary)' }} />}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
};
