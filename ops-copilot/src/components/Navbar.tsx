import React from 'react';
import {
  Radio,
  Tv,
  Bot,
  Columns,
  RotateCcw,
  Volume2,
  VolumeX,
  Gauge,
  Sparkles,
  Zap,
  CheckCircle2,
  AlertTriangle
} from 'lucide-react';
import { IncidentStage, ViewMode } from '../types';
import { sounds } from '../utils/audio';

interface NavbarProps {
  viewMode: ViewMode;
  onSetViewMode: (mode: ViewMode) => void;
  stage: IncidentStage;
  onSetStage: (stage: IncidentStage) => void;
  speed: number;
  onToggleSpeed: () => void;
  isAudioMuted: boolean;
  onToggleAudio: () => void;
  onReset: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({
  viewMode,
  onSetViewMode,
  stage,
  onSetStage,
  speed,
  onToggleSpeed,
  isAudioMuted,
  onToggleAudio,
  onReset,
}) => {
  const steps: { stage: IncidentStage; label: string; num: string }[] = [
    { stage: 'VIEWER_NORMAL', label: '1. Normal Stream', num: '1' },
    { stage: 'VIEWER_OUTAGE', label: '2. Outage Impact', num: '2' },
    { stage: 'INVESTIGATING', label: '3. Agent Investigates', num: '3' },
    { stage: 'INCIDENT_CARD', label: '4. Slack RCA Card', num: '4' },
    { stage: 'RESOLVED', label: '5. Restored', num: '5' },
  ];

  return (
    <header className="bg-[#0c111c] border-b border-slate-800 text-slate-200 px-4 py-2.5 flex flex-wrap items-center justify-between gap-4 sticky top-0 z-40 shadow-lg">
      {/* Brand & Tagline */}
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-teal-500/15 border border-teal-500/40 flex items-center justify-center text-teal-400 shadow-inner">
          <Bot className="w-5 h-5" />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <span className="font-bold text-white tracking-tight text-sm sm:text-base font-sans">
              Ops Copilot
            </span>
            <span className="text-[10px] uppercase font-mono px-1.5 py-0.2 rounded bg-teal-950/80 text-teal-300 border border-teal-700/60 font-semibold">
              Hackathon Prototype
            </span>
          </div>
          <p className="text-[11px] text-slate-400 hidden sm:block">
            AI Autonomous Incident RCA for Live Streaming
          </p>
        </div>
      </div>

      {/* Center: Stage Quick Navigation & Scrubber */}
      <div className="hidden lg:flex items-center gap-1 bg-slate-900/90 p-1 rounded-xl border border-slate-800 text-xs">
        {steps.map((s) => {
          const isActive =
            stage === s.stage || (s.stage === 'INCIDENT_CARD' && stage === 'REMEDIATING');
          return (
            <button
              key={s.stage}
              onClick={() => {
                sounds.playClick();
                onSetStage(s.stage);
              }}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 cursor-pointer ${
                isActive
                  ? 'bg-teal-600 text-white shadow-sm font-semibold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
              }`}
            >
              <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-mono ${
                isActive ? 'bg-teal-900 text-white' : 'bg-slate-800 text-slate-400'
              }`}>
                {s.num}
              </span>
              <span>{s.label.split('. ')[1]}</span>
            </button>
          );
        })}
      </div>

      {/* Right Controls: View Switcher & Demo Tools */}
      <div className="flex items-center gap-2">
        {/* View Mode Tabs */}
        <div className="flex items-center bg-slate-900/90 p-1 rounded-xl border border-slate-800 text-xs">
          <button
            onClick={() => onSetViewMode('VIEWER')}
            className={`px-2.5 py-1 rounded-lg font-medium transition-colors flex items-center gap-1.5 cursor-pointer ${
              viewMode === 'VIEWER'
                ? 'bg-slate-800 text-teal-300 font-semibold border border-slate-700'
                : 'text-slate-400 hover:text-slate-200'
            }`}
            title="Switch to Viewer View"
          >
            <Tv className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Viewer View</span>
          </button>

          <button
            onClick={() => onSetViewMode('OPS_COPILOT')}
            className={`px-2.5 py-1 rounded-lg font-medium transition-colors flex items-center gap-1.5 cursor-pointer ${
              viewMode === 'OPS_COPILOT'
                ? 'bg-slate-800 text-teal-300 font-semibold border border-slate-700'
                : 'text-slate-400 hover:text-slate-200'
            }`}
            title="Switch to Ops Copilot Dashboard"
          >
            <Bot className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Ops Copilot</span>
          </button>

          <button
            onClick={() => onSetViewMode('SPLIT')}
            className={`px-2.5 py-1 rounded-lg font-medium transition-colors flex items-center gap-1.5 cursor-pointer ${
              viewMode === 'SPLIT'
                ? 'bg-teal-600 text-white font-semibold'
                : 'text-slate-400 hover:text-slate-200'
            }`}
            title="Side-by-side Dual Presentation Mode"
          >
            <Columns className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Split Demo</span>
          </button>
        </div>

        {/* Speed / Fast Demo Toggle */}
        <button
          onClick={onToggleSpeed}
          className={`p-1.5 rounded-lg border text-xs font-mono transition-colors flex items-center gap-1 cursor-pointer ${
            speed === 2
              ? 'bg-amber-950/80 border-amber-700 text-amber-300'
              : 'bg-slate-900 border-slate-800 text-slate-400 hover:text-slate-200'
          }`}
          title="Demo Animation Speed"
        >
          <Gauge className="w-3.5 h-3.5" />
          <span className="text-[11px] font-bold">{speed}x</span>
        </button>

        {/* Sound Toggle */}
        <button
          onClick={onToggleAudio}
          className="p-1.5 rounded-lg bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
          title={isAudioMuted ? 'Unmute sounds' : 'Mute sounds'}
        >
          {isAudioMuted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4 text-teal-400" />}
        </button>

        {/* Reset Demo Button */}
        <button
          id="reset-demo-btn"
          onClick={() => {
            sounds.playClick();
            onReset();
          }}
          className="p-1.5 px-2.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium border border-slate-700 transition-colors flex items-center gap-1.5 cursor-pointer"
          title="Reset back to start"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Reset</span>
        </button>
      </div>
    </header>
  );
};
