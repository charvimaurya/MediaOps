import React, { useState } from 'react';
import {
  X,
  Activity,
  FileText,
  Layers,
  Server,
  TrendingUp,
  Cpu,
  Globe,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  ShieldCheck,
  Zap,
  Clock,
  Terminal,
  BarChart2
} from 'lucide-react';
import { IncidentReport } from '../types';

interface TelemetryDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  report: IncidentReport;
  onApplyMitigation: () => void;
}

export const TelemetryDrawer: React.FC<TelemetryDrawerProps> = ({
  isOpen,
  onClose,
  report,
  onApplyMitigation,
}) => {
  const [activeTab, setActiveTab] = useState<'metrics' | 'logs' | 'topology' | 'trace'>('metrics');

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-fade-in">
      <div className="relative w-full max-w-4xl max-h-[90vh] flex flex-col bg-[#111827] border border-slate-700 rounded-2xl shadow-2xl overflow-hidden text-slate-200">
        {/* Header */}
        <div className="px-6 py-4 bg-[#161f30] border-b border-slate-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-teal-500/10 border border-teal-500/30 text-teal-400">
              <Activity className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-bold text-white font-sans">
                  Deep Telemetry & Root Cause Analysis
                </h2>
                <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-red-950/80 border border-red-800 text-red-300 font-semibold">
                  {report.severity}
                </span>
                <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-teal-950 border border-teal-700 text-teal-300">
                  {report.confidenceScore}% AI Confidence
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Incident ID: <span className="font-mono text-slate-300">{report.id}</span> • Scope: {report.impactScope}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="px-6 border-b border-slate-800 bg-[#0f1624] flex gap-2">
          {[
            { id: 'metrics', label: 'Telemetry Metrics & Spikes', icon: BarChart2 },
            { id: 'logs', label: 'Correlated Log Lines (504s)', icon: Terminal },
            { id: 'trace', label: 'Distributed Traces (P99)', icon: Layers },
            { id: 'topology', label: 'CDN Edge Topology', icon: Globe },
          ].map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={`py-3 px-3 text-xs font-medium border-b-2 flex items-center gap-2 transition-colors cursor-pointer ${
                  isActive
                    ? 'border-teal-500 text-teal-300 bg-slate-800/30'
                    : 'border-transparent text-slate-400 hover:text-slate-200'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

        {/* Modal Body */}
        <div className="p-6 overflow-y-auto custom-scrollbar flex-1 space-y-6 text-sm">
          {/* Summary Box */}
          <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 flex flex-col md:flex-row gap-4 justify-between items-start md:items-center">
            <div>
              <span className="text-[11px] font-mono uppercase tracking-wider text-teal-400 font-semibold block mb-1">
                Synthesized Root Cause
              </span>
              <p className="text-slate-200 text-sm font-medium leading-relaxed">
                {report.likelyRootCause}
              </p>
            </div>
            <div className="shrink-0">
              <span className="text-xs text-slate-400 block mb-1">Impacted Edge POPs</span>
              <div className="flex gap-1.5">
                {report.affectedPops.map((pop) => (
                  <span
                    key={pop}
                    className="px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-300 text-xs font-mono"
                  >
                    {pop}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* TAB CONTENT: Metrics */}
          {activeTab === 'metrics' && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="p-4 rounded-xl bg-[#141d2e] border border-slate-800">
                  <span className="text-xs text-slate-400 block mb-1">5xx HLS Error Rate</span>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold text-red-400 font-mono">14.8%</span>
                    <span className="text-xs text-red-300 font-mono font-medium">+1,420%</span>
                  </div>
                  <span className="text-[11px] text-slate-400 mt-1 block">Baseline: 0.05% threshold</span>
                  {/* Simulated Sparkline */}
                  <div className="h-8 mt-3 flex items-end gap-1">
                    {[10, 12, 10, 14, 11, 15, 20, 85, 95, 98, 92].map((v, i) => (
                      <div
                        key={i}
                        className={`flex-1 rounded-t ${i >= 7 ? 'bg-red-500' : 'bg-slate-700'}`}
                        style={{ height: `${v}%` }}
                      />
                    ))}
                  </div>
                </div>

                <div className="p-4 rounded-xl bg-[#141d2e] border border-slate-800">
                  <span className="text-xs text-slate-400 block mb-1">P99 Manifest Latency</span>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold text-amber-400 font-mono">4,120 ms</span>
                    <span className="text-xs text-amber-300 font-mono font-medium">98x Normal</span>
                  </div>
                  <span className="text-[11px] text-slate-400 mt-1 block">Normal: 42ms TTFB</span>
                  {/* Simulated Sparkline */}
                  <div className="h-8 mt-3 flex items-end gap-1">
                    {[15, 14, 16, 12, 15, 18, 25, 90, 100, 94, 88].map((v, i) => (
                      <div
                        key={i}
                        className={`flex-1 rounded-t ${i >= 7 ? 'bg-amber-500' : 'bg-slate-700'}`}
                        style={{ height: `${v}%` }}
                      />
                    ))}
                  </div>
                </div>

                <div className="p-4 rounded-xl bg-[#141d2e] border border-slate-800">
                  <span className="text-xs text-slate-400 block mb-1">Smart TV Client Underruns</span>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold text-teal-400 font-mono">78.4%</span>
                    <span className="text-xs text-slate-300 font-mono font-medium">Samsung/LG</span>
                  </div>
                  <span className="text-[11px] text-slate-400 mt-1 block">Tizen 6.0 & webOS 5.2</span>
                  <div className="mt-3 space-y-1.5">
                    <div className="flex justify-between text-[11px] text-slate-400">
                      <span>Samsung Tizen</span>
                      <span className="font-mono text-slate-200">46%</span>
                    </div>
                    <div className="w-full bg-slate-800 rounded-full h-1.5">
                      <div className="bg-teal-500 h-1.5 rounded-full" style={{ width: '46%' }} />
                    </div>
                  </div>
                </div>
              </div>

              {/* Evidence Detail Cards */}
              <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800">
                <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3">
                  Autonomous Agent Verification Trail
                </h4>
                <div className="space-y-3">
                  {report.evidenceItems.map((item, idx) => (
                    <div
                      key={idx}
                      className="p-3 rounded-lg bg-[#141d2e] border border-slate-800/80 flex items-start justify-between gap-4"
                    >
                      <div className="flex items-start gap-3">
                        <div className="w-5 h-5 rounded-full bg-teal-500/10 text-teal-400 flex items-center justify-center text-xs font-mono shrink-0 mt-0.5">
                          {idx + 1}
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-semibold text-slate-200 text-xs">{item.title}</span>
                            <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-teal-300">
                              {item.badge}
                            </span>
                          </div>
                          <p className="text-xs text-slate-400 mt-1">{item.description}</p>
                        </div>
                      </div>
                      <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-800/60 shrink-0">
                        Verified
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* TAB CONTENT: Correlated Logs */}
          {activeTab === 'logs' && (
            <div className="space-y-3 font-mono text-xs">
              <div className="p-3 rounded-lg bg-black/70 border border-slate-800 text-slate-300 space-y-2 overflow-x-auto">
                <div className="text-red-400 font-semibold flex items-center gap-2">
                  <span className="px-1.5 py-0.2 rounded bg-red-950 border border-red-800 text-[10px]">
                    ERROR 504
                  </span>
                  <span>[sin-01.edge-cdn] GET /live/championship/master.m3u8 HTTP/2</span>
                </div>
                <div className="text-slate-400 pl-4 border-l border-slate-800 space-y-1">
                  <div>timestamp: 2026-08-16T16:04:12.842Z</div>
                  <div>upstream_response_time: 5.002s (origin timeout limit: 5.000s)</div>
                  <div>client_ip: 103.24.88.19 (Singapore Telecom)</div>
                  <div>user_agent: Tizen/6.0 (SMART-TV; LINUX; Tizen 6.0) OTT-Player/4.12</div>
                  <div className="text-amber-300">
                    cache_status: MISS (Reason: bypass_header `X-Cache-Purge-Deploy-v2.14`)
                  </div>
                </div>
              </div>

              <div className="p-3 rounded-lg bg-black/70 border border-slate-800 text-slate-300 space-y-2 overflow-x-auto">
                <div className="text-amber-400 font-semibold flex items-center gap-2">
                  <span className="px-1.5 py-0.2 rounded bg-amber-950 border border-amber-800 text-[10px]">
                    WARN
                  </span>
                  <span>[origin-apac-01] Ingress Thread Pool Saturation (99.4% queue depth)</span>
                </div>
                <div className="text-slate-400 pl-4 border-l border-slate-800 space-y-1">
                  <div>active_workers: 512 / 512 (exhausted)</div>
                  <div>queued_manifest_queries: 14,208 requests</div>
                  <div className="text-teal-300">
                    agent_correlation: Origin flooded because edge POPs ceased serving cached .m3u8 chunks.
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB CONTENT: Distributed Trace */}
          {activeTab === 'trace' && (
            <div className="p-4 rounded-xl bg-slate-900/80 border border-slate-800 space-y-3 font-mono text-xs">
              <div className="flex justify-between items-center text-slate-400 border-b border-slate-800 pb-2">
                <span>Span Hierarchy</span>
                <span>Duration (ms)</span>
              </div>
              <div className="space-y-2">
                <div className="p-2 rounded bg-red-950/40 border border-red-900/50 flex justify-between items-center">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-red-500" />
                    <span className="text-red-300 font-bold">HLS.Player.FetchManifest (Client)</span>
                  </div>
                  <span className="text-red-400 font-bold">4,120 ms</span>
                </div>
                <div className="pl-4">
                  <div className="p-2 rounded bg-slate-800/60 border border-slate-700/50 flex justify-between items-center">
                    <span className="text-slate-300">├── CDN.Edge.RouteHandler (sin-01)</span>
                    <span className="text-amber-400">4,098 ms</span>
                  </div>
                </div>
                <div className="pl-8">
                  <div className="p-2 rounded bg-red-950/60 border border-red-800/80 flex justify-between items-center">
                    <span className="text-red-200">└── Origin.Ingress.GeneratePlaylist (apac-origin)</span>
                    <span className="text-red-400 font-bold">5,000 ms (TIMEOUT)</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB CONTENT: Topology */}
          {activeTab === 'topology' && (
            <div className="p-4 rounded-xl bg-slate-900/80 border border-slate-800 space-y-4">
              <div className="flex items-center justify-between text-xs text-slate-400">
                <span>APAC CDN Edge Distribution Network</span>
                <span className="text-teal-400 font-mono">Target: Fastly / Cloudflare Failover</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="p-3 rounded-lg bg-red-950/50 border border-red-800 text-center">
                  <Server className="w-6 h-6 text-red-400 mx-auto mb-2" />
                  <span className="font-mono text-xs text-red-200 font-semibold block">POP sin-01</span>
                  <span className="text-[11px] text-red-400">Status: Origin Storm (100% Saturation)</span>
                </div>
                <div className="p-3 rounded-lg bg-red-950/50 border border-red-800 text-center">
                  <Server className="w-6 h-6 text-red-400 mx-auto mb-2" />
                  <span className="font-mono text-xs text-red-200 font-semibold block">POP hkg-03</span>
                  <span className="text-[11px] text-red-400">Status: Origin Storm (98% Saturation)</span>
                </div>
                <div className="p-3 rounded-lg bg-emerald-950/50 border border-emerald-800 text-center">
                  <Server className="w-6 h-6 text-emerald-400 mx-auto mb-2" />
                  <span className="font-mono text-xs text-emerald-200 font-semibold block">Backup CDN (Fastly)</span>
                  <span className="text-[11px] text-emerald-300">Ready for Instant Divert</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="px-6 py-4 bg-[#141c2c] border-t border-slate-700 flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs text-slate-400">
            Recommended Action: <strong className="text-slate-200">{report.mitigationRecommendation}</strong>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium transition-colors"
            >
              Close Drawer
            </button>
            <button
              id="apply-mitigation-modal-btn"
              onClick={() => {
                onClose();
                onApplyMitigation();
              }}
              className="px-4 py-2 rounded-lg bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold shadow-lg shadow-teal-900/30 transition-all flex items-center gap-2 cursor-pointer"
            >
              <Zap className="w-3.5 h-3.5 text-amber-200 fill-amber-200" />
              <span>Acknowledge & Execute Mitigation</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
