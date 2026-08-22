import { IncidentReport, Scenario } from './types';

interface StatusEvent {
  type: 'status';
  text: string;
}

interface ErrorEvent {
  type: 'error';
  message: string;
}

interface DoneEvent {
  type: 'done';
  report: string;
  narrative: string;
  root_cause: string | null;
  confidence: number | null;
  evidence: string[];
  region: string | null;
  viewers_affected: string | null;
  revenue_at_risk_usd_per_min: string | null;
  posted_to_slack: boolean;
  slack_error: string | null;
}

type StreamEvent = StatusEvent | ErrorEvent | DoneEvent;

/**
 * Kicks off a real investigation and streams it live: `onStatus` fires for
 * each tool call/response as the agent works, and the returned promise
 * resolves with the final parsed report once the agent is done (including
 * the Slack post outcome).
 */
export async function streamInvestigation(
  scenario: Pick<Scenario, 'service' | 'hint'>,
  onStatus: (text: string) => void
): Promise<IncidentReport> {
  const res = await fetch('/api/investigate/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ service: scenario.service, hint: scenario.hint }),
  });

  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `Request failed with status ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let newlineIndex: number;
    while ((newlineIndex = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (!line) continue;

      const event: StreamEvent = JSON.parse(line);

      if (event.type === 'status') {
        onStatus(event.text);
      } else if (event.type === 'error') {
        throw new Error(event.message);
      } else if (event.type === 'done') {
        return {
          report: event.report,
          narrative: event.narrative,
          rootCause: event.root_cause,
          confidence: event.confidence,
          evidence: event.evidence,
          region: event.region,
          viewersAffected: event.viewers_affected,
          revenueAtRiskUsdPerMin: event.revenue_at_risk_usd_per_min,
          postedToSlack: event.posted_to_slack,
          slackError: event.slack_error,
        };
      }
    }
  }

  throw new Error('Stream ended without a final report');
}
