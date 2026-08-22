export type IncidentStage =
  | 'VIEWER_NORMAL'
  | 'VIEWER_OUTAGE'
  | 'INVESTIGATING'
  | 'INCIDENT_CARD'
  | 'REMEDIATING'
  | 'RESOLVED';

export type ViewMode = 'VIEWER' | 'OPS_COPILOT' | 'SPLIT';

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

/** A selectable investigation scenario — drives what's sent to the agent. */
export interface Scenario {
  id: string;
  label: string;
  service: string;
  hint: string;
}

/** Mirrors api.py's InvestigateResponse — this is what the real agent returns. */
export interface IncidentReport {
  report: string;
  narrative: string;
  rootCause: string | null;
  confidence: number | null;
  evidence: string[];
  region: string | null;
  viewersAffected: string | null;
  revenueAtRiskUsdPerMin: string | null;
  postedToSlack: boolean;
  slackError: string | null;
}
