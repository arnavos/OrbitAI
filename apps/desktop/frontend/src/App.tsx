import React from 'react';
import { Activity, ShieldCheck, Terminal, Cpu, HardDrive } from 'lucide-react';

export const App: React.FC = () => {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '24px', gap: '20px' }}>
      {/* Header */}
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingBottom: '16px', borderBottom: '1px solid var(--border-subtle)' }}>
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 600, letterSpacing: '-0.02em', display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ color: 'var(--accent-primary)', fontSize: '24px' }}>⬡</span> ORBIT
            <span style={{ fontSize: '11px', padding: '2px 6px', background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)', color: 'var(--text-muted)' }}>
              M0 Foundation
            </span>
          </h1>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
            Engineering Architecture & Runtime Foundation
          </p>
        </div>
        <div className="status-badge" style={{ background: 'rgba(99, 102, 241, 0.1)', color: 'var(--accent-primary)', border: '1px solid var(--border-accent)' }}>
          <span className="status-dot" style={{ backgroundColor: 'var(--accent-primary)' }}></span>
          STAGE 0.2 SCAFFOLD
        </div>
      </header>

      {/* Main Content Area */}
      <main style={{ flex: 1, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
        <div className="glass-panel" style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: 'var(--accent-primary)' }}>
            <Terminal size={18} />
            <h2 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>Tauri 2 Native Host</h2>
          </div>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Rust-native desktop container managing windowing, WKWebView, and the Python backend child process lifecycle.
          </p>
          <div style={{ marginTop: 'auto', fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
            Architecture: Apple Silicon (aarch64)
          </div>
        </div>

        <div className="glass-panel" style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: '#10b981' }}>
            <Cpu size={18} />
            <h2 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>Python 3.13 Backend</h2>
          </div>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            FastAPI local daemon managed with uv. Exclusively bound to 127.0.0.1 with session-token authentication.
          </p>
          <div style={{ marginTop: 'auto', fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
            Runtime: Pinned Python 3.13.9
          </div>
        </div>

        <div className="glass-panel" style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: '#38bdf8' }}>
            <HardDrive size={18} />
            <h2 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>SQLite WAL Persistence</h2>
          </div>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Single-file zero-daemon transactional database with Write-Ahead Logging and Alembic reproducible schema migrations.
          </p>
          <div style={{ marginTop: 'auto', fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
            Location: com.orbit.app/data/orbit.db
          </div>
        </div>

        <div className="glass-panel" style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: '#ec4899' }}>
            <ShieldCheck size={18} />
            <h2 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>Security & Sandboxing</h2>
          </div>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Strict Content Security Policy (CSP), origin validation, process watchdog, and zero blind execution gates.
          </p>
          <div style={{ marginTop: 'auto', fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
            Network: Loopback-only (127.0.0.1)
          </div>
        </div>
      </main>

      {/* Status Bar */}
      <footer className="glass-panel" style={{ padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Activity size={14} style={{ color: 'var(--status-healthy)' }} />
          <span style={{ color: 'var(--text-secondary)' }}>System Status:</span>
          <span style={{ fontWeight: 500, color: 'var(--status-healthy)', fontFamily: 'var(--font-mono)' }}>SCAFFOLD READY</span>
        </div>
        <div style={{ display: 'flex', gap: '16px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
          <span>MEMORY BUDGET: 16 GB</span>
          <span>COST: ₹0/MO</span>
        </div>
      </footer>
    </div>
  );
};

export default App;
