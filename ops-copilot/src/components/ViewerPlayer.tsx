import React, { useEffect, useRef, useState } from 'react';
import {
  Play,
  Pause,
  Volume2,
  VolumeX,
  Maximize2,
  Settings,
  Tv,
  AlertTriangle,
  RefreshCw,
  Sparkles,
  Zap,
  Activity,
  CheckCircle2,
  Users,
  Radio,
  Clock
} from 'lucide-react';
import { IncidentStage, StreamTelemetry } from '../types';
import { sounds } from '../utils/audio';

interface ViewerPlayerProps {
  stage: IncidentStage;
  onSimulateOutage: () => void;
  onGoToOps: () => void;
  onReset: () => void;
  isCompact?: boolean;
}

export const ViewerPlayer: React.FC<ViewerPlayerProps> = ({
  stage,
  onSimulateOutage,
  onGoToOps,
  onReset,
  isCompact = false,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(true);
  const [isMuted, setIsMuted] = useState(false);
  const [volume, setVolume] = useState(80);
  const [matchMinute, setMatchMinute] = useState(84);
  const [matchSecond, setMatchSecond] = useState(19);

  // Match clock ticker
  useEffect(() => {
    if (stage === 'VIEWER_OUTAGE') return; // clock freezes during outage
    const interval = setInterval(() => {
      setMatchSecond((prevSec) => {
        if (prevSec >= 59) {
          setMatchMinute((m) => m + 1);
          return 0;
        }
        return prevSec + 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [stage]);

  // Animated Sports Simulation on HTML Canvas
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationFrameId: number;
    let frame = 0;

    // Simulation ball and players
    let ballX = canvas.width / 2;
    let ballY = canvas.height / 2;
    let ballVx = 3.2;
    let ballVy = 1.8;

    const players = [
      { x: 120, y: 140, vx: 0.8, team: 'blue' },
      { x: 190, y: 220, vx: 0.6, team: 'blue' },
      { x: 260, y: 110, vx: 1.1, team: 'blue' },
      { x: 340, y: 180, vx: 0.5, team: 'blue' },
      { x: 420, y: 250, vx: 0.9, team: 'blue' },
      { x: 520, y: 130, vx: -0.7, team: 'red' },
      { x: 600, y: 200, vx: -1.2, team: 'red' },
      { x: 670, y: 120, vx: -0.8, team: 'red' },
      { x: 740, y: 240, vx: -0.5, team: 'red' },
      { x: 810, y: 170, vx: -0.9, team: 'red' },
    ];

    const render = () => {
      frame++;
      const width = canvas.width;
      const height = canvas.height;

      // Clear field
      ctx.fillStyle = '#0f3d2e'; // stadium grass
      ctx.fillRect(0, 0, width, height);

      // Grass mowing stripe lines
      const stripeCount = 10;
      const stripeWidth = width / stripeCount;
      for (let i = 0; i < stripeCount; i++) {
        ctx.fillStyle = i % 2 === 0 ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.05)';
        ctx.fillRect(i * stripeWidth, 0, stripeWidth, height);
      }

      // Pitch lines
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
      ctx.lineWidth = 2;

      // Outer boundary & center line
      ctx.strokeRect(30, 20, width - 60, height - 40);
      ctx.beginPath();
      ctx.moveTo(width / 2, 20);
      ctx.lineTo(width / 2, height - 20);
      ctx.stroke();

      // Center circle
      ctx.beginPath();
      ctx.arc(width / 2, height / 2, 50, 0, Math.PI * 2);
      ctx.stroke();

      // Center spot
      ctx.fillStyle = '#fff';
      ctx.beginPath();
      ctx.arc(width / 2, height / 2, 3, 0, Math.PI * 2);
      ctx.fill();

      // Penalty boxes
      ctx.strokeRect(30, height / 2 - 60, 90, 120);
      ctx.strokeRect(width - 120, height / 2 - 60, 90, 120);

      // Goals
      ctx.fillStyle = 'rgba(255,255,255,0.6)';
      ctx.fillRect(15, height / 2 - 30, 15, 60);
      ctx.fillRect(width - 30, height / 2 - 30, 15, 60);

      // If outage: glitch / freeze / low-res degradation
      if (stage === 'VIEWER_OUTAGE') {
        // Draw static freeze with heavy pixelation & noise
        const pixelSize = 24;
        for (let x = 0; x < width; x += pixelSize) {
          for (let y = 0; y < height; y += pixelSize) {
            if (Math.random() > 0.6) {
              ctx.fillStyle = Math.random() > 0.5 ? '#1a2e22' : '#0d1f16';
              ctx.fillRect(x, y, pixelSize, pixelSize);
            }
          }
        }

        // Glitch scanlines
        for (let i = 0; i < 6; i++) {
          const scanY = (frame * 6 + i * 80) % height;
          ctx.fillStyle = 'rgba(239, 68, 68, 0.18)';
          ctx.fillRect(0, scanY, width, 12);
        }

        // Horizontal displacement band
        if (frame % 15 < 8) {
          const sliceY = (frame * 12) % (height - 50);
          const sliceImg = ctx.getImageData(0, sliceY, width, 30);
          ctx.putImageData(sliceImg, Math.sin(frame) * 20, sliceY);
        }

        animationFrameId = requestAnimationFrame(render);
        return;
      }

      // Normal movement when not in outage
      // Update players
      players.forEach((p) => {
        p.x += Math.sin(frame * 0.03 + p.y) * 0.7 + p.vx * 0.4;
        p.y += Math.cos(frame * 0.02 + p.x) * 0.5;

        // Keep inside field
        if (p.x < 60) p.x = 60;
        if (p.x > width - 60) p.x = width - 60;
        if (p.y < 40) p.y = 40;
        if (p.y > height - 40) p.y = height - 40;

        // Player shadow
        ctx.fillStyle = 'rgba(0,0,0,0.3)';
        ctx.beginPath();
        ctx.ellipse(p.x, p.y + 10, 8, 4, 0, 0, Math.PI * 2);
        ctx.fill();

        // Player body
        ctx.fillStyle = p.team === 'blue' ? '#3B82F6' : '#EF4444';
        ctx.beginPath();
        ctx.arc(p.x, p.y, 8, 0, Math.PI * 2);
        ctx.fill();

        // Jersey number / highlight
        ctx.fillStyle = '#FFFFFF';
        ctx.beginPath();
        ctx.arc(p.x, p.y - 1, 3, 0, Math.PI * 2);
        ctx.fill();
      });

      // Update ball
      ballX += ballVx;
      ballY += ballVy;
      if (ballX < 60 || ballX > width - 60) ballVx *= -1;
      if (ballY < 40 || ballY > height - 40) ballVy *= -1;

      // Ball shadow
      ctx.fillStyle = 'rgba(0,0,0,0.35)';
      ctx.beginPath();
      ctx.ellipse(ballX, ballY + 6, 5, 2.5, 0, 0, Math.PI * 2);
      ctx.fill();

      // Ball
      ctx.fillStyle = '#FFFFFF';
      ctx.beginPath();
      ctx.arc(ballX, ballY, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#111827';
      ctx.lineWidth = 1;
      ctx.stroke();

      // Camera spot lights
      const gradient = ctx.createRadialGradient(width / 2, height / 2, 50, width / 2, height / 2, width * 0.7);
      gradient.addColorStop(0, 'rgba(255,255,255,0.08)');
      gradient.addColorStop(1, 'rgba(0,0,0,0.4)');
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, width, height);

      animationFrameId = requestAnimationFrame(render);
    };

    render();

    return () => {
      cancelAnimationFrame(animationFrameId);
    };
  }, [stage]);

  const telemetry: StreamTelemetry =
    stage === 'VIEWER_OUTAGE'
      ? {
          bitrateKbps: 120,
          fps: 4,
          resolution: '144p (Degraded)',
          bufferHealthSec: 0.1,
          droppedFramesPct: 88.4,
          activeViewers: 1240100,
          edgePop: 'sin-01 (APAC Failover Attempt)',
          protocol: 'HLS (Manifest Stalled)',
          errorCode: 'ERR_HLS_MANIFEST_TIMEOUT_504',
          reconnectAttempts: 3,
        }
      : {
          bitrateKbps: 18450,
          fps: 60,
          resolution: '4K UHD (2160p60)',
          bufferHealthSec: 12.8,
          droppedFramesPct: 0.02,
          activeViewers: 1842910,
          edgePop: 'sin-01 (APAC Primary Edge)',
          protocol: 'CMAF Ultra-Low Latency',
        };

  return (
    <div className="flex flex-col h-full bg-[#0d121c] border border-slate-800 rounded-xl overflow-hidden shadow-2xl">
      {/* Top Bar for the Viewer / Broadcast header */}
      <div className="bg-[#111827] px-4 py-3 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2.5 w-2.5">
              <span
                className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${
                  stage === 'VIEWER_OUTAGE' ? 'bg-red-400' : 'bg-emerald-400'
                }`}
              />
              <span
                className={`relative inline-flex rounded-full h-2.5 w-2.5 ${
                  stage === 'VIEWER_OUTAGE' ? 'bg-red-500' : 'bg-emerald-500'
                }`}
              />
            </span>
            <span className="font-bold tracking-wider text-slate-200 uppercase text-[11px] font-mono">
              {stage === 'VIEWER_OUTAGE' ? 'SIGNAL INTERRUPTED' : 'LIVE BROADCAST'}
            </span>
          </div>

          <div className="h-4 w-px bg-slate-700 hidden sm:block" />

          <div className="hidden sm:flex items-center gap-1.5 text-slate-300 font-medium">
            <Tv className="w-3.5 h-3.5 text-teal-400" />
            <span>World Championship Final 2026</span>
          </div>
        </div>

        {/* Action Controls & Outage Trigger */}
        <div className="flex items-center gap-2">
          {stage === 'VIEWER_NORMAL' && (
            <button
              id="simulate-outage-btn"
              onClick={() => {
                sounds.playAlert();
                onSimulateOutage();
              }}
              className="group relative inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg bg-gradient-to-r from-red-600 to-rose-600 hover:from-red-500 hover:to-rose-500 text-white font-medium text-xs shadow-lg shadow-red-900/30 transition-all duration-200 active:scale-95 cursor-pointer"
            >
              <Zap className="w-3.5 h-3.5 text-amber-200 fill-amber-200 group-hover:animate-bounce" />
              <span>Simulate Outage</span>
            </button>
          )}

          {stage === 'VIEWER_OUTAGE' && (
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-1 rounded bg-red-950/80 border border-red-800 text-red-300 font-mono text-[11px] flex items-center gap-1.5 animate-pulse">
                <AlertTriangle className="w-3 h-3 text-red-400" />
                Degraded Stream
              </span>
              <button
                id="viewer-go-ops-btn"
                onClick={onGoToOps}
                className="px-3 py-1.5 rounded-lg bg-teal-600 hover:bg-teal-500 text-white font-medium text-xs shadow-md transition-colors flex items-center gap-1.5"
              >
                <span>Inspect in Ops Copilot</span>
                <span className="text-teal-200">→</span>
              </button>
            </div>
          )}

          {(stage === 'RESOLVED' || stage === 'REMEDIATING') && (
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-1 rounded bg-emerald-950/80 border border-emerald-700 text-emerald-300 font-mono text-[11px] flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                {stage === 'REMEDIATING' ? 'Mitigating Stream...' : 'Stream Restored'}
              </span>
              <button
                onClick={onReset}
                className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition-colors flex items-center gap-1"
                title="Restart flow simulation"
              >
                <RefreshCw className="w-3 h-3" />
                <span>Replay</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Main Video Viewport Canvas */}
      <div className="relative flex-1 bg-black min-h-[320px] flex items-center justify-center overflow-hidden select-none">
        {/* Canvas for dynamic pitch simulation */}
        <canvas
          ref={canvasRef}
          width={880}
          height={480}
          className="w-full h-full object-cover"
        />

        {/* Live Sports Score Overlay */}
        <div className="absolute top-4 left-4 z-20 flex items-center gap-2 bg-slate-900/85 backdrop-blur-md border border-slate-700/80 px-3 py-1.5 rounded-lg shadow-xl text-white">
          <div className="flex items-center gap-2 font-bold tracking-tight text-xs">
            <span className="text-blue-400">TITANS</span>
            <span className="bg-slate-800 px-2 py-0.5 rounded text-amber-300 font-mono text-sm font-extrabold">
              2 - 1
            </span>
            <span className="text-rose-400">GALAXY</span>
          </div>
          <div className="h-3 w-px bg-slate-700" />
          <div className="text-[11px] font-mono text-slate-300 flex items-center gap-1">
            <Clock className="w-3 h-3 text-teal-400" />
            <span>{matchMinute}:{matchSecond.toString().padStart(2, '0')}'</span>
          </div>
        </div>

        {/* Audience Count & Quality Badge */}
        <div className="absolute top-4 right-4 z-20 flex items-center gap-2">
          <div className="bg-slate-900/85 backdrop-blur-md border border-slate-700/80 px-2.5 py-1 rounded-md text-[11px] font-mono text-slate-300 flex items-center gap-1.5">
            <Users className="w-3 h-3 text-teal-400" />
            <span>{(telemetry.activeViewers / 1000000).toFixed(2)}M viewers</span>
          </div>
          <div
            className={`px-2.5 py-1 rounded-md text-[11px] font-mono font-semibold backdrop-blur-md border transition-all ${
              stage === 'VIEWER_OUTAGE'
                ? 'bg-red-950/90 border-red-700 text-red-300 animate-pulse'
                : 'bg-teal-950/80 border-teal-700/80 text-teal-300'
            }`}
          >
            {telemetry.resolution}
          </div>
        </div>

        {/* OUTAGE DEGRADATION OVERLAY */}
        {stage === 'VIEWER_OUTAGE' && (
          <div className="absolute inset-0 z-30 bg-black/75 backdrop-blur-[3px] flex flex-col items-center justify-center p-6 text-center animate-fade-in">
            {/* Spinning Loader */}
            <div className="relative mb-5">
              <div className="w-16 h-16 rounded-full border-4 border-slate-700 border-t-red-500 animate-spin" />
              <AlertTriangle className="w-6 h-6 text-red-400 absolute inset-0 m-auto" />
            </div>

            {/* Error messaging as experienced by a real viewer */}
            <div className="max-w-md bg-slate-900/90 border border-red-900/80 rounded-xl p-4 shadow-2xl mb-4">
              <h3 className="text-white font-semibold text-base flex items-center justify-center gap-2 text-red-400 mb-1">
                <span>Playback Error</span>
                <span className="text-xs px-2 py-0.5 rounded bg-red-950 border border-red-800 text-red-300 font-mono">
                  504 Gateway Timeout
                </span>
              </h3>
              <p className="text-slate-300 text-xs mb-3">
                Reconnecting to edge broadcast stream (Attempt 3 of 5)... Audio/video packet underrun detected.
              </p>
              <div className="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                <div className="bg-red-500 h-full w-2/5 animate-pulse" />
              </div>
            </div>

            {/* Callout explaining the viewer perspective */}
            <div className="bg-slate-950/90 border border-slate-800 rounded-lg px-4 py-2.5 max-w-lg text-slate-400 text-xs flex items-center gap-3">
              <div className="w-2 h-2 rounded-full bg-amber-400 shrink-0 animate-ping" />
              <span className="text-left">
                <strong className="text-slate-200">The Viewer Experience:</strong> Viewer sees a degraded 144p frozen stream with no context. Next, watch how Ops Copilot investigates the outage autonomously in seconds.
              </span>
            </div>
          </div>
        )}

        {/* RESOLVED CELEBRATION BADGE */}
        {stage === 'RESOLVED' && (
          <div className="absolute bottom-16 right-4 z-20 bg-emerald-950/90 border border-emerald-600/80 px-3.5 py-2 rounded-lg text-emerald-200 text-xs shadow-xl flex items-center gap-2 backdrop-blur-md animate-bounce">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            <span className="font-medium">Stream Restored — 4K 60fps Zero Jitter</span>
          </div>
        )}
      </div>

      {/* Realistic Video Player Controls Bar */}
      <div className="bg-[#0f1523] px-4 py-3 border-t border-slate-800 flex flex-col gap-2">
        {/* Timeline Progress Bar */}
        <div className="relative w-full h-1.5 bg-slate-800 rounded-full overflow-hidden cursor-pointer group">
          {/* Buffer track */}
          <div
            className={`absolute top-0 left-0 h-full transition-all duration-300 ${
              stage === 'VIEWER_OUTAGE' ? 'w-24 bg-red-900/60' : 'w-full bg-slate-700'
            }`}
          />
          {/* Playhead */}
          <div
            className={`absolute top-0 left-0 h-full transition-all duration-300 ${
              stage === 'VIEWER_OUTAGE' ? 'w-20 bg-red-500' : 'w-full bg-teal-500'
            }`}
          />
        </div>

        {/* Control Buttons */}
        <div className="flex items-center justify-between text-slate-300 text-xs">
          <div className="flex items-center gap-4">
            <button
              onClick={() => setIsPlaying(!isPlaying)}
              className="hover:text-white transition-colors"
              aria-label="Toggle Play"
            >
              {isPlaying && stage !== 'VIEWER_OUTAGE' ? (
                <Pause className="w-4 h-4" />
              ) : (
                <Play className="w-4 h-4" />
              )}
            </button>

            <div className="flex items-center gap-2">
              <button
                onClick={() => setIsMuted(!isMuted)}
                className="hover:text-white transition-colors"
                aria-label="Toggle Mute"
              >
                {isMuted || stage === 'VIEWER_OUTAGE' ? (
                  <VolumeX className="w-4 h-4 text-red-400" />
                ) : (
                  <Volume2 className="w-4 h-4" />
                )}
              </button>
              <input
                type="range"
                min="0"
                max="100"
                value={stage === 'VIEWER_OUTAGE' ? 0 : isMuted ? 0 : volume}
                onChange={(e) => {
                  setVolume(Number(e.target.value));
                  setIsMuted(false);
                }}
                className="w-16 h-1 bg-slate-700 accent-teal-500 rounded cursor-pointer"
              />
            </div>

            <div className="font-mono text-[11px] text-slate-400 flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-red-500" />
              <span className="text-slate-200">LIVE</span>
              <span className="text-slate-500">|</span>
              <span>-00:00</span>
            </div>
          </div>

          <div className="flex items-center gap-3 text-slate-400">
            <div className="font-mono text-[11px] text-slate-400 hidden sm:block">
              {telemetry.bitrateKbps > 1000
                ? `${(telemetry.bitrateKbps / 1000).toFixed(1)} Mbps`
                : `${telemetry.bitrateKbps} Kbps`}{' '}
              • {telemetry.fps} fps
            </div>
            <button className="hover:text-white transition-colors" title="Stream settings">
              <Settings className="w-3.5 h-3.5" />
            </button>
            <button className="hover:text-white transition-colors" title="Fullscreen">
              <Maximize2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
