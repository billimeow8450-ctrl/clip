import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Clock, Scissors, Play, ChevronLeft, ChevronRight, RotateCcw } from 'lucide-react';

function formatTime(seconds) {
  if (isNaN(seconds) || seconds < 0) return '00:00:00';
  const s = Math.floor(seconds);
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

export default function TimelineTrimmer({ duration = 600, startTime = 0, endTime = 60, onChange }) {
  const containerRef = useRef(null);
  const [activeDragging, setActiveDragging] = useState(null); // 'start', 'end', or null

  const startPercent = duration > 0 ? (startTime / duration) * 100 : 0;
  const endPercent = duration > 0 ? (endTime / duration) * 100 : 100;
  const selectedDuration = Math.max(0, endTime - startTime);

  const handlePointerDown = (type, e) => {
    e.preventDefault();
    e.stopPropagation();
    setActiveDragging(type);
  };

  const handlePointerMove = useCallback((e) => {
    if (!activeDragging || !containerRef.current || duration <= 0) return;

    const rect = containerRef.current.getBoundingClientRect();
    const clientX = e.clientX || (e.touches && e.touches[0]?.clientX);
    if (clientX === undefined) return;

    const offsetX = Math.max(0, Math.min(clientX - rect.left, rect.width));
    const newSeconds = Math.round((offsetX / rect.width) * duration);

    if (activeDragging === 'start') {
      const clamped = Math.max(0, Math.min(newSeconds, endTime - 1));
      onChange(clamped, endTime);
    } else if (activeDragging === 'end') {
      const clamped = Math.max(startTime + 1, Math.min(newSeconds, duration));
      onChange(startTime, clamped);
    }
  }, [activeDragging, duration, startTime, endTime, onChange]);

  const handlePointerUp = useCallback(() => {
    setActiveDragging(null);
  }, []);

  useEffect(() => {
    if (activeDragging) {
      window.addEventListener('pointermove', handlePointerMove);
      window.addEventListener('pointerup', handlePointerUp);
      window.addEventListener('touchmove', handlePointerMove);
      window.addEventListener('touchend', handlePointerUp);
    }
    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      window.removeEventListener('touchmove', handlePointerMove);
      window.removeEventListener('touchend', handlePointerUp);
    };
  }, [activeDragging, handlePointerMove, handlePointerUp]);

  const adjustTime = (target, amount) => {
    if (target === 'start') {
      const newStart = Math.max(0, Math.min(startTime + amount, endTime - 1));
      onChange(newStart, endTime);
    } else {
      const newEnd = Math.max(startTime + 1, Math.min(endTime + amount, duration));
      onChange(startTime, newEnd);
    }
  };

  return (
    <div className="theme-card p-6">
      {/* Header Info & Precise Time Display */}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
        <div>
          <div className="text-[10px] font-mono font-bold uppercase tracking-[0.12em] text-[#0f766e] mb-1">
            RANGE SPECIFICATION
          </div>
          <h3 className="font-sans text-base font-bold text-[#0f172a] flex items-center gap-2">
            <Scissors className="w-4 h-4 text-[#0f766e]" />
            Timeline Range Extraction
          </h3>
        </div>

        {/* Formatted Metrics Chips */}
        <div className="flex items-center gap-2.5">
          <div className="theme-well py-1.5 px-3 flex items-center gap-2">
            <span className="text-[11px] font-semibold text-[#526173] uppercase">Selected:</span>
            <span className="font-mono text-xs font-bold text-[#0f766e] tabular-nums">
              {formatTime(selectedDuration)}
            </span>
          </div>
          <div className="theme-well py-1.5 px-3 flex items-center gap-2">
            <span className="text-[11px] font-semibold text-[#526173] uppercase">Full Length:</span>
            <span className="font-mono text-xs font-semibold text-[#0f172a] tabular-nums">
              {formatTime(duration)}
            </span>
          </div>
        </div>
      </div>

      {/* Main Timeline Track */}
      <div className="relative mb-5">
        <div
          ref={containerRef}
          className="timeline-track-container relative overflow-hidden cursor-pointer"
        >
          {/* Subtle audio/video timeline markers */}
          <div className="absolute inset-0 opacity-20 flex items-center justify-between px-2 gap-1 pointer-events-none">
            {Array.from({ length: 50 }).map((_, i) => (
              <div
                key={i}
                className="w-0.5 bg-[#526173] rounded-full"
                style={{ height: `${20 + ((i * 19) % 60)}%` }}
              />
            ))}
          </div>

          {/* Selected Range Highlight */}
          <div
            className="timeline-selected-range"
            style={{
              left: `${startPercent}%`,
              width: `${Math.max(1, endPercent - startPercent)}%`,
            }}
          />

          {/* START Drag Handle */}
          <div
            onPointerDown={(e) => handlePointerDown('start', e)}
            className="timeline-handle group"
            style={{ left: `calc(${startPercent}% - 10px)` }}
            title="Drag to set Start Time"
          >
            <div className="timeline-handle-bar" />
            <div className="absolute -top-8 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 bg-[#0f172a] text-white font-mono text-[10px] font-bold py-1 px-1.5 rounded transition-opacity pointer-events-none whitespace-nowrap shadow-md">
              {formatTime(startTime)}
            </div>
          </div>

          {/* END Drag Handle */}
          <div
            onPointerDown={(e) => handlePointerDown('end', e)}
            className="timeline-handle group"
            style={{ left: `calc(${endPercent}% - 10px)` }}
            title="Drag to set End Time"
          >
            <div className="timeline-handle-bar" />
            <div className="absolute -top-8 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 bg-[#0f172a] text-white font-mono text-[10px] font-bold py-1 px-1.5 rounded transition-opacity pointer-events-none whitespace-nowrap shadow-md">
              {formatTime(endTime)}
            </div>
          </div>
        </div>

        {/* Time Ticks */}
        <div className="flex justify-between text-[11px] font-mono text-[#64748b] mt-2 px-1 tabular-nums">
          <span>00:00:00</span>
          <span>{formatTime(duration * 0.25)}</span>
          <span>{formatTime(duration * 0.5)}</span>
          <span>{formatTime(duration * 0.75)}</span>
          <span>{formatTime(duration)}</span>
        </div>
      </div>

      {/* Numerical Coordinate Cards with Fine Tuning Controls */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* Start Coordinate */}
        <div className="theme-well p-3.5 flex items-center justify-between">
          <div>
            <div className="text-[10px] font-mono font-bold text-[#526173] uppercase tracking-wider">Start Point (T0)</div>
            <div className="font-mono text-base font-bold text-[#0f172a] mt-0.5 tabular-nums">
              {formatTime(startTime)}
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => adjustTime('start', -5)}
              className="btn-secondary min-h-[30px] py-1 px-2 text-[11px] font-mono"
            >
              -5s
            </button>
            <button
              type="button"
              onClick={() => adjustTime('start', -1)}
              className="btn-secondary min-h-[30px] py-1 px-2 text-[11px] font-mono"
            >
              -1s
            </button>
            <button
              type="button"
              onClick={() => adjustTime('start', 1)}
              className="btn-secondary min-h-[30px] py-1 px-2 text-[11px] font-mono"
            >
              +1s
            </button>
            <button
              type="button"
              onClick={() => adjustTime('start', 5)}
              className="btn-secondary min-h-[30px] py-1 px-2 text-[11px] font-mono"
            >
              +5s
            </button>
          </div>
        </div>

        {/* End Coordinate */}
        <div className="theme-well p-3.5 flex items-center justify-between">
          <div>
            <div className="text-[10px] font-mono font-bold text-[#526173] uppercase tracking-wider">End Point (T1)</div>
            <div className="font-mono text-base font-bold text-[#0f172a] mt-0.5 tabular-nums">
              {formatTime(endTime)}
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => adjustTime('end', -5)}
              className="btn-secondary min-h-[30px] py-1 px-2 text-[11px] font-mono"
            >
              -5s
            </button>
            <button
              type="button"
              onClick={() => adjustTime('end', -1)}
              className="btn-secondary min-h-[30px] py-1 px-2 text-[11px] font-mono"
            >
              -1s
            </button>
            <button
              type="button"
              onClick={() => adjustTime('end', 1)}
              className="btn-secondary min-h-[30px] py-1 px-2 text-[11px] font-mono"
            >
              +1s
            </button>
            <button
              type="button"
              onClick={() => adjustTime('end', 5)}
              className="btn-secondary min-h-[30px] py-1 px-2 text-[11px] font-mono"
            >
              +5s
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
