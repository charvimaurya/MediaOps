import React, { useState } from 'react';
import {
  ShieldAlert,
  Bot,
  CheckCircle2,
  Clock,
  Sparkles,
  Search,
  ExternalLink,
  Check,
  AlertTriangle,
  ArrowRight,
  TrendingUp,
  Activity,
  Layers,
  Tv,
  FileCode,
  Zap,
  Terminal,
  ChevronRight,
  Send,
  MessageSquare,
  Hash,
  CornerDownRight
} from 'lucide-react';
import { IncidentReport, IncidentStage, InvestigationStep } from '../types';
import { sounds } from '../utils/audio';

interface OpsDashboardProps {
  stage: IncidentStage;
  steps: InvestigationStep[];
  activeStepIndex: number;
  report: IncidentReport;
  onViewDetails: () => void;
  onAcknowledge: () => void;
  onSwitchToViewer: () => void;
}

export const OpsDashboard: React.FC<OpsDashboardProps> = ({
  stage,
  steps,
  activeStepIndex,
  report,
  onViewDetails,
  onAcknowledge,
  onSwitchToViewer,
}) => {
  const [hasAcknowledged, setHasAcknowledged] = useState(false);

  const isInvestigating = stage === 'INVESTIGATING';
  const isCardReady = stage === 'INCIDENT_CARD' || stage === 'REMEDIATING' || stage === 'RESOLVED';

  return (
    <div className="flex flex-col h-full bg-[#0E131F] border border-slate-800 rounded-xl overflow-hidden shadow-2xl text-slate-100 font-sans">
      {/* Ops Copilot Top Header */}
      <div className="bg-[#141B2D] px-5 py-3.5 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-teal-500/10 border border-teal-500/30 flex items-center justify-center text-teal-400">
            <Bot className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-sm font-bold text-white tracking-tight flex items-center gap-1.5">
                <span>Ops Copilot</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-teal-950 text-teal-300 border border-teal-700/60 font-mono font-medium">
                  AI Autonomous Agent
                </span>
              </h1>
            </div>
            <p className="text-xs text-slate-400">
              Live Broadcast Telemetry & Autonomous Root-Cause Analysis
            </p>
          </div>
        </div>

        {/* Status indicator */}
        <div className="flex items-center gap-3">
          <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-900/90 border border-slate-800 text-xs text-slate-300 font-mono">
            <Clock className="w-3.5 h-3.5 text-teal-400" />
            <span>
              {stage === 'INVESTIGATING'
                ? 'Investigating: ~3.8s elapsed'
                : stage === 'INCIDENT_CARD'
                ? 'RCA Completed in 4.2s (vs ~25m manual)'
                : stage === 'REMEDIATING'
                ? 'Executing Automated Failover...'
                : stage === 'RESOLVED'
                ? 'Incident Mitigated & Resolved'
                : 'System Monitoring (Normal)'}
            </span>
          </div>

          <button
            onClick={onSwitchToViewer}
            className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium transition-colors flex items-center gap-1.5"
          >
            <Tv className="w-3.5 h-3.5 text-teal-400" />
            <span>Viewer View</span>
          </button>
        </div>
      </div>

      {/* Main Dashboard Workspace */}
      <div className="flex-1 p-5 md:p-6 overflow-y-auto custom-scrollbar space-y-5 bg-[#0B0F19]">
        {/* 1. ALERT BANNER */}
        {stage !== 'VIEWER_NORMAL' && (
          <div className="relative overflow-hidden rounded-xl border border-red-800/80 bg-gradient-to-r from-red-950/70 via-red-900/30 to-slate-900/90 p-4 shadow-xl animate-fade-in">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-start sm:items-center gap-3">
                <div className="p-2 rounded-lg bg-red-900/40 border border-red-700/60 text-red-400 shrink-0">
                  <ShieldAlert className="w-5 h-5 animate-pulse" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-red-900/80 text-red-200 border border-red-700">
                      P1 Alert
                    </span>
                    <h2 className="text-sm sm:text-base font-bold text-white">
                      Error rate spike detected — APAC region
                    </h2>
                  </div>
                  <p className="text-xs text-red-300/80 mt-1 font-mono">
                    Affected Edge POPs: <span className="text-red-200">sin-01 (Singapore), hkg-03 (Hong Kong)</span> • HLS 504 Timeouts: <span className="text-red-200">+1,420%</span>
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2 self-end sm:self-center">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-950/80 border border-slate-800 text-[11px] font-mono text-slate-300">
                  <span className="w-2 h-2 rounded-full bg-red-500 animate-ping" />
                  Live Incident #INC-8942
                </span>
              </div>
            </div>
          </div>
        )}

        {/* 2. AUTONOMOUS INVESTIGATION ANIMATION SEQUENCE */}
        {(stage === 'INVESTIGATING' || isCardReady) && (
          <div className="p-5 rounded-xl bg-[#111827] border border-slate-800 shadow-xl space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-teal-500/20 text-teal-400 flex items-center justify-center">
                  <Sparkles className="w-3.5 h-3.5" />
                </div>
                <div>
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 font-mono">
                    Autonomous Agent Diagnostic Trail
                  </h3>
                  <p className="text-[11px] text-slate-400">
                    Investigating error logs, distributed traces, and client breakdown in parallel
                  </p>
                </div>
              </div>

              {stage === 'INVESTIGATING' ? (
                <div className="flex items-center gap-2 text-xs font-mono text-teal-400">
                  <span className="w-2 h-2 rounded-full bg-teal-400 animate-ping" />
                  <span>Agent Active...</span>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 text-xs font-mono text-emerald-400 bg-emerald-950/60 px-2.5 py-1 rounded border border-emerald-800/60">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>4/4 Checks Completed</span>
                </div>
              )}
            </div>

            {/* Steps Timeline Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
              {steps.map((step, idx) => {
                const isCurrent = isInvestigating && idx === activeStepIndex;
                const isDone = isCardReady || idx < activeStepIndex;
                const isPending = !isDone && !isCurrent;

                return (
                  <div
                    key={step.id}
                    className={`relative p-3.5 rounded-xl border transition-all duration-300 flex flex-col justify-between min-h-[110px] ${
                      isDone
                        ? 'bg-[#152136] border-teal-500/40 text-slate-200 shadow-sm'
                        : isCurrent
                        ? 'bg-[#18263e] border-teal-400 shadow-lg shadow-teal-950/50 ring-1 ring-teal-500/50 scale-[1.02]'
                        : 'bg-slate-900/40 border-slate-800/80 text-slate-400 opacity-60'
                    }`}
                  >
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] font-mono uppercase font-bold text-slate-400">
                          Step 0{step.id}
                        </span>
                        {isDone && (
                          <span className="text-emerald-400 flex items-center gap-1 text-[11px] font-mono">
                            <Check className="w-3.5 h-3.5" />
                            <span>Done</span>
                          </span>
                        )}
                        {isCurrent && (
                          <span className="text-teal-400 flex items-center gap-1 text-[11px] font-mono animate-pulse">
                            <div className="w-2 h-2 rounded-full bg-teal-400 animate-ping" />
                            <span>Checking...</span>
                          </span>
                        )}
                        {isPending && (
                          <span className="text-slate-400 text-[11px] font-mono">Queued</span>
                        )}
                      </div>

                      <h4 className="text-xs font-bold text-white mb-1">
                        {step.label}
                      </h4>
                      <p className="text-[11px] text-slate-400 leading-tight">
                        {step.subtext}
                      </p>
                    </div>

                    {/* Discovered finding pill */}
                    {(isDone || isCurrent) && (
                      <div className="mt-2.5 pt-2 border-t border-slate-800/80">
                        <span className="text-[10px] font-mono text-teal-300 block truncate">
                          💡 {step.discoveredFinding}
                        </span>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* 3. RESULT — INCIDENT CARD (Styled like a Slack / Ops Bot Message) */}
        {isCardReady && (
          <div className="p-5 md:p-6 rounded-xl bg-[#131B2C] border border-slate-700/80 shadow-2xl space-y-4 animate-fade-in">
            {/* Slack-style Message Header */}
            <div className="flex items-start gap-3.5">
              <div className="relative shrink-0">
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-teal-500 to-emerald-600 flex items-center justify-center text-white shadow-md">
                  <Bot className="w-5 h-5" />
                </div>
                <span className="absolute -bottom-1 -right-1 w-4 h-4 bg-[#131B2C] rounded-full flex items-center justify-center">
                  <span className="w-2.5 h-2.5 bg-emerald-400 rounded-full" />
                </span>
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-bold text-white text-sm">Ops Copilot Bot</span>
                  <span className="px-1.5 py-0.2 rounded bg-teal-950 border border-teal-800 text-[10px] font-mono text-teal-300">
                    APP
                  </span>
                  <span className="text-xs text-slate-400 flex items-center gap-1 font-mono">
                    <Hash className="w-3 h-3 text-slate-400" />
                    <span>ops-live-incidents</span>
                    <span>• Just now</span>
                  </span>
                </div>

                {/* Slack Incident Card Content Block with Left Border Accent */}
                <div className="mt-3 p-4 rounded-xl bg-[#172238] border-l-4 border-l-teal-500 border border-slate-700/60 shadow-lg space-y-3.5">
                  {/* Card Title & Severity */}
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
                      <h3 className="font-bold text-white text-sm sm:text-base">
                        Incident detected: playback errors, APAC region
                      </h3>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="px-2.5 py-1 rounded-full text-xs font-mono font-bold bg-teal-950 text-teal-300 border border-teal-700 flex items-center gap-1 shadow-sm">
                        <Sparkles className="w-3 h-3 text-teal-400" />
                        <span>{report.confidenceScore}% Confidence</span>
                      </span>
                    </div>
                  </div>

                  {/* Section: Likely Root Cause */}
                  <div className="bg-[#121a2a] p-3.5 rounded-lg border border-slate-800">
                    <span className="text-[11px] font-mono uppercase font-bold tracking-wider text-teal-400 block mb-1">
                      Likely Root Cause
                    </span>
                    <p className="text-slate-200 text-xs sm:text-sm leading-relaxed">
                      {report.likelyRootCause}
                    </p>
                  </div>

                  {/* Section: Evidence Checked (3 bullet points) */}
                  <div className="space-y-1.5">
                    <span className="text-[11px] font-mono uppercase font-bold tracking-wider text-slate-400 block mb-1">
                      Evidence Checked by Agent
                    </span>
                    <ul className="space-y-2 text-xs">
                      {report.evidenceItems.map((item, idx) => (
                        <li
                          key={idx}
                          className="flex items-start gap-2 text-slate-300 bg-[#121a2a]/60 px-3 py-2 rounded-lg border border-slate-800/80"
                        >
                          <CheckCircle2 className="w-4 h-4 text-teal-400 shrink-0 mt-0.5" />
                          <div className="flex-1">
                            <strong className="text-white font-medium">{item.title}: </strong>
                            <span className="text-slate-300">{item.description}</span>
                            <span className="ml-2 font-mono text-[10px] px-1.5 py-0.2 rounded bg-slate-800 text-teal-300">
                              {item.badge}
                            </span>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>

                  {/* Remediation note */}
                  <div className="text-xs text-slate-300 flex items-center gap-2 pt-1">
                    <CornerDownRight className="w-3.5 h-3.5 text-teal-400 shrink-0" />
                    <span>
                      Recommended Action: <strong className="text-white">{report.mitigationRecommendation}</strong>
                    </span>
                  </div>

                  {/* Action Buttons as requested */}
                  <div className="pt-2 border-t border-slate-700/60 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex flex-wrap items-center gap-2">
                      {/* Button 1: View Details */}
                      <button
                        id="view-details-btn"
                        onClick={() => {
                          sounds.playClick();
                          onViewDetails();
                        }}
                        className="px-3.5 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold border border-slate-700 transition-colors flex items-center gap-1.5 cursor-pointer shadow-sm"
                      >
                        <Search className="w-3.5 h-3.5 text-teal-400" />
                        <span>View details</span>
                        <ExternalLink className="w-3 h-3 text-slate-400 ml-0.5" />
                      </button>

                      {/* Button 2: Acknowledge & Remediate */}
                      <button
                        id="acknowledge-btn"
                        onClick={() => {
                          sounds.playSuccess();
                          setHasAcknowledged(true);
                          onAcknowledge();
                        }}
                        disabled={stage === 'REMEDIATING' || stage === 'RESOLVED'}
                        className={`px-4 py-2 rounded-lg text-xs font-bold transition-all flex items-center gap-2 cursor-pointer shadow-md ${
                          stage === 'RESOLVED'
                            ? 'bg-emerald-900/60 border border-emerald-700 text-emerald-300'
                            : stage === 'REMEDIATING'
                            ? 'bg-teal-700 text-white animate-pulse'
                            : 'bg-teal-600 hover:bg-teal-500 text-white shadow-teal-900/30 active:scale-95'
                        }`}
                      >
                        {stage === 'RESOLVED' ? (
                          <>
                            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                            <span>Acknowledged & Mitigated</span>
                          </>
                        ) : stage === 'REMEDIATING' ? (
                          <>
                            <Zap className="w-4 h-4 text-amber-300 animate-spin" />
                            <span>Diverting APAC Traffic & Flushing Cache...</span>
                          </>
                        ) : (
                          <>
                            <Check className="w-4 h-4" />
                            <span>Acknowledge & Apply Fix</span>
                          </>
                        )}
                      </button>
                    </div>

                    {/* Quick switch to viewer */}
                    {stage === 'RESOLVED' && (
                      <button
                        onClick={onSwitchToViewer}
                        className="px-3 py-1.5 rounded-lg bg-emerald-950 border border-emerald-700 text-emerald-200 text-xs font-medium hover:bg-emerald-900 transition-colors flex items-center gap-1.5"
                      >
                        <span>Return to Live Stream (Restored)</span>
                        <ArrowRight className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                </div>

                {/* Slack Reaction Emojis for realism */}
                <div className="mt-2.5 flex items-center gap-2 text-xs">
                  <span className="px-2 py-0.5 rounded-full bg-slate-800/80 border border-slate-700 text-slate-300 flex items-center gap-1 text-[11px]">
                    <span>👀</span>
                    <span>3 engineers reading</span>
                  </span>
                  <span className="px-2 py-0.5 rounded-full bg-slate-800/80 border border-slate-700 text-slate-300 flex items-center gap-1 text-[11px]">
                    <span>⚡</span>
                    <span>1 auto-action queued</span>
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* When in normal state prior to outage */}
        {stage === 'VIEWER_NORMAL' && (
          <div className="p-8 rounded-xl bg-[#111827] border border-slate-800 text-center space-y-4 max-w-xl mx-auto my-12">
            <div className="w-12 h-12 rounded-2xl bg-teal-500/10 border border-teal-500/30 text-teal-400 flex items-center justify-center mx-auto">
              <Bot className="w-6 h-6" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white">
                Ops Copilot is actively monitoring stream telemetry
              </h3>
              <p className="text-xs text-slate-400 mt-1 max-w-md mx-auto">
                No active incidents. The video stream is playing normally at 4K UHD 60fps. Switch to Viewer View to simulate an incident.
              </p>
            </div>
            <button
              onClick={onSwitchToViewer}
              className="px-4 py-2 rounded-lg bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold shadow-md transition-colors"
            >
              Open Viewer View & Trigger Outage
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
