import { Scenario } from './types';

// Only the scenario labels/services/hints are simulated — each one drives a
// genuinely different real query to the live agent (Gemini + Grafana MCP).
export const SCENARIOS: Scenario[] = [
  {
    id: 'encoding-apac',
    label: 'Encoding errors (APAC)',
    service: 'encoding-pipeline',
    hint: 'Look for elevated error patterns in the last hour and tell me the likely root cause.',
  },
  {
    id: 'latency-apac',
    label: 'Latency spike (APAC)',
    service: 'playback-api',
    hint:
      'Look for unusually slow or high-latency requests in the last hour and tell me the likely root cause.',
  },
  {
    id: 'device-mobile',
    label: 'Device breakdown (Mobile)',
    service: 'playback-api',
    hint:
      'Look for elevated errors specifically affecting mobile devices in the last hour and tell me the likely root cause.',
  },
];
