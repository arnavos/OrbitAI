import { useEffect, useState } from 'react';
import { backendClient, type BackendConnectionInfo } from '../services/backend';
import type { ConnectionStatus, HealthResponse } from '../types/contracts';

export interface UseBackendState {
  status: ConnectionStatus;
  connection: BackendConnectionInfo | null;
  health: HealthResponse | null;
  port: string | null;
}

export function useBackend(): UseBackendState {
  const [status, setStatus] = useState<ConnectionStatus>('INITIALIZING');
  const [connection, setConnection] = useState<BackendConnectionInfo | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [port, setPort] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    let unlistenRestart: (() => void) | undefined;
    let unlistenCrash: (() => void) | undefined;

    async function init() {
      if (!backendClient.isDesktop()) {
        if (isMounted) {
          setStatus('STANDALONE DEV');
        }
        return;
      }

      if (isMounted) {
        setStatus('CONNECTING');
      }

      try {
        const conn = await backendClient.getConnection();
        if (!isMounted) return;

        if (!conn) {
          setStatus('INITIALIZATION TIMEOUT');
          return;
        }

        setConnection(conn);
        try {
          const parsed = new URL(conn.apiUrl);
          setPort(parsed.port);
        } catch {
          // ignore parsing error
        }

        const h = await backendClient.checkHealth();
        if (!isMounted) return;

        if (h && h.status === 'healthy') {
          setHealth(h);
          setStatus('CONNECTED');
        } else {
          setStatus('HEALTH CHECK FAILED');
        }
      } catch (err) {
        if (isMounted) {
          console.error('Backend connection error:', err);
          setStatus('HEALTH CHECK FAILED');
        }
      }

      // Register desktop event listeners for automatic crash recovery and fatal crash
      try {
        const { listen } = await import('@tauri-apps/api/event');
        if (!isMounted) return;

        const restartCleanup = await listen<BackendConnectionInfo>(
          'orbit://backend-restarted',
          async (event) => {
            console.log('[Orbit Frontend] Backend restart detected:', event.payload);
            backendClient.setConnection(event.payload);
            if (!isMounted) return;

            setConnection(event.payload);
            try {
              const parsed = new URL(event.payload.apiUrl);
              setPort(parsed.port);
            } catch {
              // ignore
            }

            try {
              const h = await backendClient.checkHealth();
              if (isMounted && h && h.status === 'healthy') {
                setHealth(h);
                setStatus('CONNECTED');
              }
            } catch {
              if (isMounted) setStatus('HEALTH CHECK FAILED');
            }
          }
        );
        unlistenRestart = restartCleanup;

        const crashCleanup = await listen<{ reason: string; state: string }>(
          'orbit://backend-crashed',
          (event) => {
            console.error('[Orbit Frontend] Backend fatal crash:', event.payload);
            if (isMounted) {
              setStatus('FATAL_CRASH');
            }
          }
        );
        unlistenCrash = crashCleanup;
      } catch {
        // Not in Tauri or event API unavailable
      }
    }

    init();
    return () => {
      isMounted = false;
      if (unlistenRestart) unlistenRestart();
      if (unlistenCrash) unlistenCrash();
    };
  }, []);

  return { status, connection, health, port };
}
