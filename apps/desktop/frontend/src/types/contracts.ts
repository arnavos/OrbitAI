import type { components } from './api';

export type HealthResponse = components['schemas']['HealthResponse'];
export type DetailedHealthResponse = components['schemas']['DetailedHealthResponse'];
export type SystemInfoResponse = components['schemas']['SystemInfoResponse'];
export type SystemSettingResponse = components['schemas']['SystemSettingResponse'];
export type AuditEventResponse = components['schemas']['AuditEventResponse'];
export type ErrorDetail = components['schemas']['ErrorDetail'];

export interface APIEnvelope<T> {
  success: boolean;
  data: T | null;
  error?: ErrorDetail | null;
  correlation_id: string;
  timestamp?: string;
}

export type ConnectionStatus =
  | 'INITIALIZING'
  | 'CONNECTING'
  | 'CONNECTED'
  | 'STANDALONE DEV'
  | 'RECONNECTING'
  | 'HEALTH CHECK FAILED'
  | 'INITIALIZATION TIMEOUT'
  | 'DISCONNECTED';

export interface TelemetryEvent {
  type: 'TELEMETRY_UPDATE';
  data: DetailedHealthResponse;
}

export interface SystemInfoEvent {
  type: 'SYSTEM_INFO';
  data: SystemInfoResponse;
}

export interface AuditEventsEvent {
  type: 'AUDIT_EVENTS';
  data: AuditEventResponse[];
}

export interface ConnectionEstablishedEvent {
  type: 'CONNECTION_ESTABLISHED';
  uptime: number;
}

export interface PongEvent {
  type: 'pong';
  time: number;
}

export type ServerWebSocketMessage =
  | TelemetryEvent
  | SystemInfoEvent
  | AuditEventsEvent
  | ConnectionEstablishedEvent
  | PongEvent
  | { type: 'ack'; received: string };
