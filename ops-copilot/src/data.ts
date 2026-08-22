import { IncidentReport, InvestigationStep } from './types';

export const INITIAL_INVESTIGATION_STEPS: InvestigationStep[] = [
  {
    id: 1,
    label: 'Checking error logs...',
    subtext: 'Scanning edge CDN access logs & 5xx HTTP response codes',
    metricBadge: '504 Timeouts (+1,420%)',
    status: 'waiting',
    discoveredFinding: 'Spike in 504 Gateway Timeouts on /live/championship/master.m3u8',
    category: 'logs',
  },
  {
    id: 2,
    label: 'Checking latency traces...',
    subtext: 'Analyzing distributed trace spans between Edge POPs and Origin',
    metricBadge: 'P99: 4,120ms (98x)',
    status: 'waiting',
    discoveredFinding: 'P99 Manifest TTFB surged from 42ms to 4,120ms at Origin Ingress',
    category: 'traces',
  },
  {
    id: 3,
    label: 'Checking device breakdown...',
    subtext: 'Clustering buffer underruns by client OS and player SDK',
    metricBadge: '78% Smart TVs',
    status: 'waiting',
    discoveredFinding: '78% of failing sessions are Samsung Tizen & LG webOS Smart TVs',
    category: 'devices',
  },
  {
    id: 4,
    label: 'Synthesizing root cause...',
    subtext: 'Correlating git deployments, cache hit ratios, and origin saturation',
    metricBadge: '88% AI Confidence',
    status: 'waiting',
    discoveredFinding: 'Config v2.14 purged edge cache, flooding origin with master manifest requests',
    category: 'synthesis',
  },
];

export const MOCK_INCIDENT_REPORT: IncidentReport = {
  id: 'INC-8942',
  severity: 'P1 - CRITICAL',
  region: 'APAC Region (Singapore sin-01, Hong Kong hkg-03)',
  impactScope: '1.24M live sports viewers experiencing buffer underruns',
  errorRateSpike: '+1,420%',
  confidenceScore: 88,
  likelyRootCause:
    'CDN Edge cache invalidation storm on manifest playlists following v2.14 deployment, triggering origin saturation in APAC region.',
  evidenceItems: [
    {
      title: 'Error log spike',
      description: 'HLS manifest 504 timeouts surged from 0.05% to 14.8% across APAC edge POPs (sin-01, hkg-03)',
      badge: '+1,420% 504s',
      impact: 'critical',
    },
    {
      title: 'Latency increase',
      description: 'Manifest fetch P99 latency jumped 98x from 42ms to 4,120ms, exhausting client playback buffers',
      badge: '4,120ms P99',
      impact: 'critical',
    },
    {
      title: 'Device breakdown',
      description: '78% of impacted clients are Smart TV apps (Samsung Tizen / LG webOS) lacking multi-CDN failover fallback',
      badge: '78% Smart TV OTT',
      impact: 'warning',
    },
  ],
  mitigationRecommendation:
    'Divert APAC manifest traffic to Secondary CDN (Fastly POPs) and pin Cache-Control max-age to 6s.',
  affectedPops: ['sin-01 (Singapore)', 'hkg-03 (Hong Kong)', 'tyo-02 (Tokyo)'],
  affectedDevices: [
    { name: 'Samsung Tizen TV', percentage: 46 },
    { name: 'LG webOS TV', percentage: 32 },
    { name: 'Android TV / FireTV', percentage: 14 },
    { name: 'Mobile / Web Browser', percentage: 8 },
  ],
};
