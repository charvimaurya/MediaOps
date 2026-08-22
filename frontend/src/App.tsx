import React, { useState, useEffect, useRef } from 'react';
import { IncidentStage, ViewMode, IncidentReport } from './types';
import { SCENARIOS } from './data';
import { streamInvestigation } from './api';
import { Navbar } from './components/Navbar';
import { ViewerPlayer } from './components/ViewerPlayer';
import { OpsDashboard } from './components/OpsDashboard';
import { TelemetryDrawer } from './components/TelemetryDrawer';
import { sounds } from './utils/audio';
import { Sparkles, Tv, Bot } from 'lucide-react';

export default function App() {
  const [stage, setStage] = useState<IncidentStage>('VIEWER_NORMAL');
  const [viewMode, setViewMode] = useState<ViewMode>('VIEWER');
  const [speed, setSpeed] = useState<number>(1);
  const [isAudioMuted, setIsAudioMuted] = useState<boolean>(false);
  const [isDrawerOpen, setIsDrawerOpen] = useState<boolean>(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [report, setReport] = useState<IncidentReport | null>(null);
  const [investigationError, setInvestigationError] = useState<string | null>(null);
  const [statusLog, setStatusLog] = useState<string[]>([]);
  const [scenarioId, setScenarioId] = useState<string>(SCENARIOS[0].id);
  const investigationInFlight = useRef(false);

  // Audio mute sync
  useEffect(() => {
    sounds.enabled = !isAudioMuted;
  }, [isAudioMuted]);

  // Show temporary toast message on key transitions
  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => {
      setToastMessage((prev) => (prev === msg ? null : prev));
    }, 3500);
  };

  // Fire the real investigation exactly once per INVESTIGATING entry,
  // streaming live tool-call status into statusLog as the agent works.
  useEffect(() => {
    if (stage !== 'INVESTIGATING') {
      investigationInFlight.current = false;
      return;
    }
    if (investigationInFlight.current) return;
    investigationInFlight.current = true;

    setInvestigationError(null);
    setStatusLog([]);

    const scenario = SCENARIOS.find((s) => s.id === scenarioId) ?? SCENARIOS[0];

    streamInvestigation(scenario, (text) => {
      sounds.playStepDone();
      setStatusLog((prev) => [...prev, text]);
    })
      .then((result) => {
        setReport(result);
        setStage('INCIDENT_CARD');
        const confidenceMsg =
          result.confidence != null ? `${result.confidence}% confidence` : 'unknown confidence';
        showToast(
          result.postedToSlack
            ? `✨ RCA synthesized (${confidenceMsg}) and posted to Slack`
            : `✨ RCA synthesized (${confidenceMsg}) — Slack post failed`
        );
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        setInvestigationError(message);
        setStage('VIEWER_OUTAGE');
        showToast(`⚠️ Investigation failed: ${message}`);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  // Auto transition from Outage to Ops Copilot if user stays on Viewer view
  useEffect(() => {
    let timer: NodeJS.Timeout;

    if (stage === 'VIEWER_OUTAGE') {
      showToast('⚠️ Playback error detected! Switching to Ops Copilot...');
      timer = setTimeout(() => {
        setStage('INVESTIGATING');
        if (viewMode === 'VIEWER') {
          setViewMode('OPS_COPILOT');
        }
      }, 3000 / speed);
    }

    return () => clearTimeout(timer);
  }, [stage, speed, viewMode]);

  // Trigger Outage
  const handleSimulateOutage = () => {
    setStage('VIEWER_OUTAGE');
  };

  // Immediate jump to Ops
  const handleGoToOps = () => {
    setStage('INVESTIGATING');
    setViewMode('OPS_COPILOT');
  };

  // Acknowledge and Remediate
  const handleAcknowledgeAndRemediate = () => {
    setStage('REMEDIATING');
    showToast('⚡ Executing automated CDN failover to secondary edge...');
    sounds.playAlert();

    setTimeout(() => {
      setStage('RESOLVED');
      sounds.playSuccess();
      showToast('✅ Stream fully restored to 4K 60fps!');

      // Automatically flip back to viewer after short delay so presenter sees loop closed
      setTimeout(() => {
        if (viewMode === 'OPS_COPILOT') {
          setViewMode('VIEWER');
        }
      }, 1600 / speed);
    }, 2000 / speed);
  };

  // Reset entire flow
  const handleReset = () => {
    setStage('VIEWER_NORMAL');
    setViewMode('VIEWER');
    setIsDrawerOpen(false);
    setReport(null);
    setInvestigationError(null);
    setStatusLog([]);
    showToast('Demo reset to initial normal broadcast state');
  };

  // Manual stage jump from scrubber
  const handleSetStage = (targetStage: IncidentStage) => {
    // No real report yet — route through a real investigation instead of
    // showing a card with nothing in it.
    if ((targetStage === 'INCIDENT_CARD' || targetStage === 'RESOLVED') && !report) {
      targetStage = 'INVESTIGATING';
    }

    setStage(targetStage);
    if (targetStage === 'INVESTIGATING' && viewMode === 'VIEWER') {
      setViewMode('OPS_COPILOT');
    }
  };

  const handleToggleSpeed = () => {
    setSpeed((s) => (s === 1 ? 2 : 1));
  };

  const handleToggleAudio = () => {
    setIsAudioMuted((m) => !m);
  };

  const isScenarioLocked = stage !== 'VIEWER_NORMAL';

  return (
    <div className="min-h-screen flex flex-col bg-[#080C14] text-slate-100 font-sans selection:bg-teal-500 selection:text-white">
      {/* Top Navbar Header */}
      <Navbar
        viewMode={viewMode}
        onSetViewMode={setViewMode}
        stage={stage}
        onSetStage={handleSetStage}
        speed={speed}
        onToggleSpeed={handleToggleSpeed}
        isAudioMuted={isAudioMuted}
        onToggleAudio={handleToggleAudio}
        onReset={handleReset}
      />

      {/* Quick Toast Banner */}
      {toastMessage && (
        <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 rounded-full bg-slate-900/95 border border-teal-500/50 text-white text-xs font-medium shadow-2xl backdrop-blur-md flex items-center gap-2 animate-bounce">
          <Sparkles className="w-3.5 h-3.5 text-teal-400" />
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Main Content Workspace */}
      <main className="flex-1 p-3 sm:p-5 max-w-7xl w-full mx-auto flex flex-col">
        {/* Helper Context Sub-bar for Hackathon Presenter */}
        <div className="mb-3 px-3 py-1.5 rounded-lg bg-slate-900/60 border border-slate-800 flex flex-wrap items-center justify-between text-xs text-slate-400 gap-2">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-teal-400" />
            <span>
              <strong>Ops Copilot Demo:</strong> Showing autonomous streaming incident investigation & RCA in seconds instead of 25-minute manual war rooms.
            </span>
          </div>

          <div className="flex items-center gap-3 text-[11px] font-mono">
            <label className="flex items-center gap-1.5">
              <span className="text-slate-500">Scenario:</span>
              <select
                value={scenarioId}
                onChange={(e) => setScenarioId(e.target.value)}
                disabled={isScenarioLocked}
                className="bg-slate-900 border border-slate-700 rounded px-1.5 py-0.5 text-teal-300 text-[11px] font-mono disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {SCENARIOS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>

            <span>
              Stage:{' '}
              <strong className="text-teal-300">
                {stage === 'VIEWER_NORMAL'
                  ? '1. Normal Stream'
                  : stage === 'VIEWER_OUTAGE'
                  ? '2. Outage Impact'
                  : stage === 'INVESTIGATING'
                  ? '3. Agent Investigating'
                  : stage === 'INCIDENT_CARD'
                  ? '4. Slack RCA Card'
                  : stage === 'REMEDIATING'
                  ? '5. Auto-Remediating'
                  : '6. Restored'}
              </strong>
            </span>
          </div>
        </div>

        {/* VIEW MODE 1: VIEWER VIEW ONLY */}
        {viewMode === 'VIEWER' && (
          <div className="flex-1 flex flex-col">
            <ViewerPlayer
              stage={stage}
              onSimulateOutage={handleSimulateOutage}
              onGoToOps={handleGoToOps}
              onReset={handleReset}
            />
          </div>
        )}

        {/* VIEW MODE 2: OPS COPILOT DASHBOARD ONLY */}
        {viewMode === 'OPS_COPILOT' && (
          <div className="flex-1 flex flex-col">
            <OpsDashboard
              stage={stage}
              statusLog={statusLog}
              report={report}
              investigationError={investigationError}
              onViewDetails={() => setIsDrawerOpen(true)}
              onAcknowledge={handleAcknowledgeAndRemediate}
              onSwitchToViewer={() => setViewMode('VIEWER')}
            />
          </div>
        )}

        {/* VIEW MODE 3: SPLIT SIDE-BY-SIDE PRESENTATION MODE */}
        {viewMode === 'SPLIT' && (
          <div className="flex-1 grid grid-cols-1 lg:grid-cols-12 gap-4">
            {/* Left Col: Viewer Screen */}
            <div className="lg:col-span-5 flex flex-col">
              <div className="mb-1.5 flex items-center justify-between text-xs text-slate-400">
                <span className="font-bold text-slate-300 uppercase tracking-wider text-[11px] flex items-center gap-1.5">
                  <Tv className="w-3.5 h-3.5 text-teal-400" />
                  Viewer Perspective (Client App)
                </span>
                <span className="text-[10px] font-mono text-slate-400">4K Live HLS Player</span>
              </div>
              <div className="flex-1">
                <ViewerPlayer
                  stage={stage}
                  onSimulateOutage={handleSimulateOutage}
                  onGoToOps={handleGoToOps}
                  onReset={handleReset}
                  isCompact={true}
                />
              </div>
            </div>

            {/* Right Col: Ops Copilot */}
            <div className="lg:col-span-7 flex flex-col">
              <div className="mb-1.5 flex items-center justify-between text-xs text-slate-400">
                <span className="font-bold text-slate-300 uppercase tracking-wider text-[11px] flex items-center gap-1.5">
                  <Bot className="w-3.5 h-3.5 text-teal-400" />
                  Engineering Perspective (Ops Copilot)
                </span>
                <span className="text-[10px] font-mono text-teal-400">Autonomous Diagnostic Engine</span>
              </div>
              <div className="flex-1">
                <OpsDashboard
                  stage={stage}
                  statusLog={statusLog}
                  report={report}
                  investigationError={investigationError}
                  onViewDetails={() => setIsDrawerOpen(true)}
                  onAcknowledge={handleAcknowledgeAndRemediate}
                  onSwitchToViewer={() => setViewMode('VIEWER')}
                />
              </div>
            </div>
          </div>
        )}
      </main>

      {/* Deep Telemetry Modal / Drawer */}
      <TelemetryDrawer
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        report={report}
        onApplyMitigation={handleAcknowledgeAndRemediate}
      />
    </div>
  );
}
