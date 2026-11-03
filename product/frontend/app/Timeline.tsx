'use client';

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from 'react';

import { media, type Result } from '../lib/api';

export const clock = (t: number) => {
  const m = Math.floor(t / 60);
  return `${String(m).padStart(2, '0')}:${(t - m * 60).toFixed(2).padStart(5, '0')}`;
};

const HUE: Record<string, number> = { footstep: 95, impact: 18, hit: 350, door: 38, pickup: 150, ui: 200, whoosh: 265, other: 120 };

type Wave = { data: Float32Array; rate: number; times: Record<string, number> };

/** Decodes the result video's audio so each clip can show its waveform. */
function useWave(url: string, result: Result): Wave | null {
  const [wave, setWave] = useState<Wave | null>(null);
  const events = useRef(result.events.events);
  events.current = result.events.events;
  useEffect(() => {
    let cancelled = false;
    setWave(null);
    (async () => {
      try {
        const buffer = await (await fetch(url)).arrayBuffer();
        const context = new AudioContext();
        const audio = await context.decodeAudioData(buffer);
        void context.close();
        const mono = new Float32Array(audio.length);
        for (let c = 0; c < audio.numberOfChannels; c++) {
          const channel = audio.getChannelData(c);
          for (let i = 0; i < mono.length; i++) mono[i] += channel[i] / audio.numberOfChannels;
        }
        const times = Object.fromEntries(events.current.map(e => [e.id, e.t]));
        if (!cancelled) setWave({ data: mono, rate: audio.sampleRate, times });
      } catch { /* No waveform if the browser cannot decode the audio. */ }
    })();
    return () => { cancelled = true; };
  }, [url]);
  return wave;
}

function Waveform({ wave, id, duration }: { wave: Wave; id: string; duration: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const width = Math.max(1, Math.floor(el.clientWidth)), height = Math.max(1, Math.floor(el.clientHeight));
    el.width = width; el.height = height;
    const draw = el.getContext('2d');
    if (!draw) return;
    const from = Math.floor(wave.times[id] * wave.rate), to = Math.min(wave.data.length, Math.floor((wave.times[id] + duration) * wave.rate));
    if (to <= from) return;
    const peaks: number[] = [];
    for (let x = 0; x < width; x++) {
      const a = from + Math.floor((x / width) * (to - from)), b = Math.max(a + 1, from + Math.floor(((x + 1) / width) * (to - from)));
      let peak = 0;
      for (let i = a; i < b; i++) peak = Math.max(peak, Math.abs(wave.data[i]));
      peaks.push(peak);
    }
    const scale = Math.max(...peaks, 0.02);
    draw.fillStyle = 'rgba(255,255,255,.78)';
    peaks.forEach((p, x) => { const h = Math.max(1, (p / scale) * height * 0.8); draw.fillRect(x, (height - h) / 2, 1, h); });
  }, [wave, id, duration]);
  return <canvas ref={canvas} className="wave" />;
}

export function Timeline({ result, player, version, busy, onMove, onCommit, onSeek }: {
  result: Result;
  player: RefObject<HTMLVideoElement | null>;
  version: number;
  busy: boolean;
  onMove: (id: string, t: number) => void;
  onCommit: (id: string) => void;
  onSeek: (t: number) => void;
}) {
  const { duration, fps } = result.events.video;
  const lanes = useRef<HTMLDivElement>(null);
  const playhead = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: string; grab: number; moved: boolean } | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [dragId, setDragId] = useState<string | null>(null);
  const wave = useWave(`${media(result.result_video_url)}?v=${version}`, result);

  // The playhead follows the video without re-rendering the page every frame.
  useEffect(() => {
    let frame = 0;
    const tick = () => {
      if (playhead.current && player.current) playhead.current.style.left = `${Math.min(player.current.currentTime / duration, 1) * 100}%`;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [player, duration]);

  const timeAt = (clientX: number) => {
    const box = lanes.current!.getBoundingClientRect();
    return Math.min(Math.max((clientX - box.left) / box.width, 0), 1) * duration;
  };
  const step = duration <= 12 ? 1 : duration <= 30 ? 2 : duration <= 60 ? 5 : 10;
  const ticks = Array.from({ length: Math.floor(duration / step) + 1 }, (_, i) => i * step);

  function scrub(event: PointerEvent<HTMLDivElement>) {
    if (event.type === 'pointerdown') event.currentTarget.setPointerCapture(event.pointerId);
    if (event.buttons === 1) { if (player.current) player.current.currentTime = timeAt(event.clientX); }
  }
  function startDrag(event: PointerEvent<HTMLDivElement>, id: string, t: number) {
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { id, grab: timeAt(event.clientX) - t, moved: false };
    setDragId(id);
  }
  function dragTo(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    drag.current.moved = true;
    onMove(drag.current.id, timeAt(event.clientX) - drag.current.grab);
  }
  function endDrag(event: PointerEvent<HTMLDivElement>, id: string, t: number) {
    const state = drag.current;
    drag.current = null; setDragId(null);
    event.currentTarget.releasePointerCapture(event.pointerId);
    if (state?.moved) onCommit(id); else onSeek(t);
  }
  function nudge(event: KeyboardEvent<HTMLDivElement>, id: string, t: number) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const amount = event.shiftKey ? 0.1 : 1 / fps;
    onMove(id, t + (event.key === 'ArrowLeft' ? -amount : amount));
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => onCommit(id), 600);
  }

  return <div className="daw">
    <div className="daw-row ruler-row"><div className="gutter">{clock(0)}</div>
      <div className="ruler" onPointerDown={scrub} onPointerMove={scrub}>{ticks.map(t => <span key={t} style={{ left: `${t / duration * 100}%` }}>{t}s</span>)}</div></div>
    <div className="daw-body">
      {result.events.events.map((event, i) => {
        const skipped = event.confidence < 0.5;
        const hue = HUE[event.type] ?? 120;
        return <div className="daw-row" key={event.id}>
          <div className="gutter">{i + 1}</div>
          <div className="lane" ref={i === 0 ? lanes : undefined} onPointerDown={scrub} onPointerMove={scrub}>
            <div role="slider" tabIndex={busy ? -1 : 0} aria-label={`${event.prompt}. Starts at ${event.t.toFixed(2)} seconds. Use arrow keys to move.`} aria-valuemin={0} aria-valuemax={duration} aria-valuenow={event.t}
              className={`clip${dragId === event.id ? ' dragging' : ''}${skipped ? ' skipped' : ''}`}
              style={{ left: `${event.t / duration * 100}%`, width: `${Math.max(event.duration / duration * 100, 1.5)}%`, '--h': hue } as React.CSSProperties}
              title={`${event.type} · ${event.t.toFixed(2)}s · ${event.prompt}${skipped ? ' (below 50% confidence, not mixed)' : ''}`}
              onPointerDown={e => { e.stopPropagation(); if (!busy) startDrag(e, event.id, event.t); }} onPointerMove={e => { if (!busy) dragTo(e); }}
              onPointerUp={e => { if (!busy && drag.current) endDrag(e, event.id, event.t); }} onKeyDown={e => { if (!busy) nudge(e, event.id, event.t); }}>
              {wave && wave.times[event.id] !== undefined && <Waveform wave={wave} id={event.id} duration={event.duration} />}
              <span className="clip-label">{event.prompt}</span>
              <span className={`tip${dragId === event.id ? ' show' : ''}`}>{clock(event.t)}</span>
            </div>
          </div>
        </div>;
      })}
      <div className="playhead-layer"><div className="playhead" ref={playhead} /></div>
    </div>
  </div>;
}
