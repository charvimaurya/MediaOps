import React, { useEffect, useRef, useState } from 'react';
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
  Tv,
  Zap,
  Hash
} from 'lucide-react';
import { IncidentReport, IncidentStage } from '../types';
import { sounds } from '../utils/audio';

interface OpsDashboardProps {
  stage: IncidentStage;
  statusLog: string[];
  report: IncidentReport | null;
  investigationError: string | null;
  onViewDetails: () => void;
  onAcknowledge: () => void;
  onSwitchToViewer: () => void;
}

function confidenceBadgeClasses(score: number): string {
  if (score > 80) return 'bg-emerald-950 text-emerald-300 border-emerald-700';
  if (score >= 50) return 'bg-amber-950 text-amber-300 border-amber-700';
  return 'bg-red-950 text-red-300 border-red-700';
}

export const OpsDashboard: React.FC<OpsDashboardProps> = ({
  stage,
  statusLog,
  report,
  investigationError,
  onViewDetails,
  onAcknowledge,
  onSwitchToViewer,
}) => {
  const [hasAcknowledged, setHasAcknowledged] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);

  const isInvestigating = stage === 'INVESTIGATING';
  const isCardReady =
    (stage === 'INCIDENT_CARD' || stage === 'REMEDIATING' || stage === 'RESOLVED') && !!report;

  // Auto-scroll the live status feed as new lines stream in.
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [statusLog]);

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
                ? 'Investigating live...'
                : stage === 'INCIDENT_CARD'
                ? 'RCA Completed'
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
                      Alert
                    </span>
                    <h2 className="text-sm sm:text-base font-bold text-white">
                      Elevated error patterns detected
                    </h2>
                  </div>
                  <p className="text-xs text-red-300/80 mt-1 font-mono">
                    {isInvestigating
                      ? 'Autonomous agent is investigating Grafana logs and live viewer impact now.'
                      : report
                      ? 'Investigation complete — see the report below.'
                      : 'Awaiting investigation.'}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2 self-end sm:self-center">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-950/80 border border-slate-800 text-[11px] font-mono text-slate-300">
                  <span className="w-2 h-2 rounded-full bg-red-500 animate-ping" />
                  Live Incident
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Investigation error banner */}
        {investigationError && (
          <div className="p-4 rounded-xl border border-red-800/80 bg-red-950/40 flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-red-300">Investigation failed</p>
              <p className="text-xs text-red-300/80 mt-0.5">{investigationError}</p>
            </div>
          </div>
        )}

        {/* 2. LIVE AGENT STATUS FEED */}
        {(stage === 'INVESTIGATING' || isCardReady) && (
          <div className="p-5 rounded-xl bg-[#111827] border border-slate-800 shadow-xl space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2">
                <div className="w-6 h-6 rounded-full bg-teal-500/20 text-teal-400 flex items-center justify-center">
                  <Sparkles className="w-3.5 h-3.5" />
                </div>
                <div>
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 font-mono">
                    Autonomous Agent Activity
                  </h3>
                  <p className="text-[11px] text-slate-400">
                    Live tool calls streamed from the running investigation
                  </p>
                </div>
              </div>

              {isInvestigating ? (
                <div className="flex items-center gap-2 text-xs font-mono text-teal-400">
                  <span className="w-2 h-2 rounded-full bg-teal-400 animate-ping" />
                  <span>Agent Active...</span>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 text-xs font-mono text-emerald-400 bg-emerald-950/60 px-2.5 py-1 rounded border border-emerald-800/60">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>Investigation Complete</span>
                </div>
              )}
            </div>

            {/* Scrolling live status log */}
            <div
              ref={logRef}
              className="max-h-48 overflow-y-auto custom-scrollbar font-mono text-xs space-y-1.5 pr-1"
            >
              {statusLog.length === 0 && isInvestigating && (
                <div className="text-slate-500">Connecting to agent...</div>
              )}
              {statusLog.map((line, idx) => (
                <div key={idx} className="text-slate-300">
                  {line}
                </div>
              ))}
              {isCardReady && <div className="text-emerald-400">✅ Investigation complete</div>}
            </div>
          </div>
        )}

        {/* 3. RESULT — INCIDENT CARD (Styled like a Slack / Ops Bot Message) */}
        {isCardReady && report && (
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
                    <span>
                      {report.postedToSlack
                        ? '• Posted to Slack'
                        : report.slackError
                        ? '• Slack post failed'
                        : '• Just now'}
                    </span>
                  </span>
                </div>

                {/* Slack Incident Card Content Block with Left Border Accent */}
                <div className="mt-3 p-4 rounded-xl bg-[#172238] border-l-4 border-l-teal-500 border border-slate-700/60 shadow-lg space-y-3.5">
                  {/* Root cause headline & Confidence badge */}
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                      <h3 className="font-bold text-white text-sm sm:text-base leading-snug">
                        {report.rootCause ?? 'Incident investigation complete'}
                      </h3>
                    </div>

                    {report.confidence != null && (
                      <span
                        className={`shrink-0 px-2.5 py-1 rounded-full text-xs font-mono font-bold border flex items-center gap-1 shadow-sm ${confidenceBadgeClasses(
                          report.confidence
                        )}`}
                      >
                        <Sparkles className="w-3 h-3" />
                        <span>{report.confidence}% Confidence</span>
                      </span>
                    )}
                  </div>

                  {/* Evidence bullets */}
                  {report.evidence.length > 0 && (
                    <div className="space-y-1.5">
                      <span className="text-[11px] font-mono uppercase font-bold tracking-wider text-slate-400 block mb-1">
                        Evidence
                      </span>
                      <ul className="space-y-1.5 text-xs">
                        {report.evidence.map((item, idx) => (
                          <li
                            key={idx}
                            className="flex items-start gap-2 text-slate-300 bg-[#121a2a]/60 px-3 py-2 rounded-lg border border-slate-800/80"
                          >
                            <CheckCircle2 className="w-4 h-4 text-teal-400 shrink-0 mt-0.5" />
                            <span>{item}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Impact section, visually separated */}
                  {(report.region || report.viewersAffected || report.revenueAtRiskUsdPerMin) && (
                    <div className="p-3.5 rounded-lg bg-teal-950/30 border border-teal-800/50">
                      <span className="text-[11px] font-mono uppercase font-bold tracking-wider text-teal-300 block mb-2">
                        Impact
                      </span>
                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                        {report.region && (
                          <div>
                            <span className="text-slate-400 block">Region</span>
                            <span className="text-white font-semibold">{report.region}</span>
                          </div>
                        )}
                        {report.viewersAffected && (
                          <div>
                            <span className="text-slate-400 block">Viewers affected</span>
                            <span className="text-white font-semibold">{report.viewersAffected}</span>
                          </div>
                        )}
                        {report.revenueAtRiskUsdPerMin && (
                          <div>
                            <span className="text-slate-400 block">Revenue at risk</span>
                            <span className="text-white font-semibold">
                              ${report.revenueAtRiskUsdPerMin}/min
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Fallback narrative when the agent's response couldn't be parsed */}
                  {!report.rootCause && (
                    <div className="bg-[#121a2a] p-3.5 rounded-lg border border-slate-800">
                      <p className="text-slate-200 text-xs sm:text-sm leading-relaxed whitespace-pre-wrap line-clamp-6">
                        {report.narrative}
                      </p>
                    </div>
                  )}

                  <button
                    onClick={onViewDetails}
                    className="text-[11px] font-mono text-teal-400 hover:text-teal-300 transition-colors"
                  >
                    Read full report →
                  </button>

                  {report.slackError && (
                    <div className="text-xs text-amber-300 flex items-center gap-2 pt-1">
                      <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                      <span>Slack post failed: {report.slackError}</span>
                    </div>
                  )}

                  {/* Simulated-data disclaimer */}
                  <p className="text-[10px] text-slate-500 pt-2 border-t border-slate-800/60">
                    Alert and viewer data are simulated for this demo — investigation and
                    reasoning are performed live by Gemini against real Grafana data.
                  </p>

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
                No active incidents. The video stream is playing normally at 4K UHD 60fps. Pick a
                scenario above, then switch to Viewer View to simulate an incident.
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
