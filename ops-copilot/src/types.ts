export type IncidentStage =
  | 'VIEWER_NORMAL'
  | 'VIEWER_OUTAGE'
  | 'INVESTIGATING'
  | 'INCIDENT_CARD'
  | 'REMEDIATING'
  | 'RESOLVED';

export type ViewMode = 'VIEWER' | 'OPS_COPILOT' | 'SPLIT';

export interface InvestigationStep {
  id: number;
  label: string;
  subtext: string;
  metricBadge: string;
  latencyMs?: number;
  status: 'waiting' | 'running' | 'done';
  discoveredFinding: string;
  category: 'logs' | 'traces' | 'devices' | 'synthesis';
}

export interface StreamTelemetry {
  bitrateKbps: number;
  fps: number;
  resolution: string;
  bufferHealthSec: number;
  droppedFramesPct: number;
  activeViewers: number;
  edgePop: string;
  protocol: string;
  errorCode?: string;
  reconnectAttempts?: number;
}

export interface IncidentReport {
  id: string;
  severity: 'P1 - CRITICAL' | 'P2 - MAJOR' | 'P3 - MINOR';
  region: string;
  impactScope: string;
  errorRateSpike: string;
  confidenceScore: number;
  likelyRootCause: string;
  evidenceItems: {
    title: string;
    description: string;
    badge: string;
    impact: 'critical' | 'warning' | 'info';
  }[];
  mitigationRecommendation: string;
  affectedPops: string[];
  affectedDevices: { name: string; percentage: number }[];
}
