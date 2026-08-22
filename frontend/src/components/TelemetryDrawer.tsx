import React from 'react';
import { X, Activity, Zap, Send, Bot, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { IncidentReport } from '../types';

interface TelemetryDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  report: IncidentReport | null;
  onApplyMitigation: () => void;
}

function confidenceBadgeClasses(score: number): string {
  if (score > 80) return 'bg-emerald-950 text-emerald-300 border-emerald-700';
  if (score >= 50) return 'bg-amber-950 text-amber-300 border-amber-700';
  return 'bg-red-950 text-red-300 border-red-700';
}

export const TelemetryDrawer: React.FC<TelemetryDrawerProps> = ({
  isOpen,
  onClose,
  report,
  onApplyMitigation,
}) => {
  if (!isOpen || !report) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-fade-in">
      <div className="relative w-full max-w-3xl max-h-[90vh] flex flex-col bg-[#111827] border border-slate-700 rounded-2xl shadow-2xl overflow-hidden text-slate-200">
        {/* Header */}
        <div className="px-6 py-4 bg-[#161f30] border-b border-slate-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-teal-500/10 border border-teal-500/30 text-teal-400">
              <Activity className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-bold text-white font-sans">Full Investigation Report</h2>
                {report.confidence != null && (
                  <span
                    className={`px-2 py-0.5 rounded text-[11px] font-mono border font-semibold ${confidenceBadgeClasses(
                      report.confidence
                    )}`}
                  >
                    {report.confidence}% AI Confidence
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400 mt-0.5 flex items-center gap-1.5">
                <Bot className="w-3.5 h-3.5 text-teal-400" />
                Raw output from the autonomous agent (Grafana + live impact tool)
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

        {/* Body */}
        <div className="p-6 overflow-y-auto custom-scrollbar flex-1 space-y-4 text-sm">
          {report.rootCause && (
            <div className="p-4 rounded-xl bg-[#141d2e] border border-slate-800">
              <span className="text-[11px] font-mono uppercase font-bold tracking-wider text-teal-400 block mb-1">
                Root Cause
              </span>
              <p className="text-slate-200 text-sm font-medium leading-relaxed">{report.rootCause}</p>
            </div>
          )}

          {report.evidence.length > 0 && (
            <div className="p-4 rounded-xl bg-[#141d2e] border border-slate-800">
              <span className="text-[11px] font-mono uppercase font-bold tracking-wider text-teal-400 block mb-2">
                Evidence
              </span>
              <ul className="space-y-2">
                {report.evidence.map((item, idx) => (
                  <li key={idx} className="flex items-start gap-2 text-xs text-slate-300">
                    <CheckCircle2 className="w-4 h-4 text-teal-400 shrink-0 mt-0.5" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(report.region || report.viewersAffected || report.revenueAtRiskUsdPerMin) && (
            <div className="p-4 rounded-xl bg-teal-950/30 border border-teal-800/50">
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
                    <span className="text-white font-semibold">${report.revenueAtRiskUsdPerMin}/min</span>
                  </div>
                )}
              </div>
            </div>
          )}

          <div>
            <span className="text-[11px] font-mono uppercase font-bold tracking-wider text-slate-400 block mb-1">
              Raw Agent Narrative
            </span>
            <div className="p-4 rounded-xl bg-[#141d2e] border border-slate-800 whitespace-pre-wrap text-slate-300 text-xs leading-relaxed font-mono">
              {report.narrative}
            </div>
          </div>

          {report.slackError && (
            <div className="p-3 rounded-lg bg-amber-950/40 border border-amber-800/60 text-amber-300 text-xs flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>Slack post failed: {report.slackError}</span>
            </div>
          )}

          {report.postedToSlack && (
            <div className="p-3 rounded-lg bg-emerald-950/40 border border-emerald-800/60 text-emerald-300 text-xs flex items-center gap-2">
              <Send className="w-4 h-4 shrink-0" />
              <span>This report was posted to Slack.</span>
            </div>
          )}

          <p className="text-[10px] text-slate-500 pt-1">
            Alert and viewer data are simulated for this demo — investigation and reasoning are
            performed live by Gemini against real Grafana data.
          </p>
        </div>

        {/* Footer Actions */}
        <div className="px-6 py-4 bg-[#141c2c] border-t border-slate-700 flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium transition-colors"
          >
            Close
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
  );
};
