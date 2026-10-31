import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { Card } from './Card';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught exception:', error, errorInfo);
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  public render() {
    if (this.state.hasError) {
      return (
        <Card style={{ margin: '24px', border: '1px solid var(--status-error)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--status-error)' }}>
            <AlertTriangle size={20} />
            <h2 style={{ fontSize: '16px', fontWeight: 600 }}>Frontend Runtime Exception</h2>
          </div>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            An unexpected error occurred while rendering the interface.
          </p>
          <pre
            style={{
              padding: '12px',
              background: 'var(--bg-tertiary)',
              borderRadius: 'var(--radius-sm)',
              fontSize: '11px',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-muted)',
              overflowX: 'auto',
            }}
          >
            {this.state.error?.message || 'Unknown error'}
          </pre>
          <button
            onClick={this.handleReset}
            style={{
              alignSelf: 'flex-start',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-primary)',
              cursor: 'pointer',
              fontSize: '12px',
            }}
          >
            <RotateCcw size={14} /> Retry Component
          </button>
        </Card>
      );
    }

    return this.props.children;
  }
}
