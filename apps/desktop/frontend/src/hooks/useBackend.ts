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
    }

    init();
    return () => {
      isMounted = false;
    };
  }, []);

  return { status, connection, health, port };
}
