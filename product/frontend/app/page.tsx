'use client';

import { useEffect, useRef, useState } from 'react';

import { API, media, request, validateVideo, type Job, type Result } from '../lib/api';

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollingPaused, setPollingPaused] = useState(false);
  const [pollAttempt, setPollAttempt] = useState(0);
  const resultPlayer = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!file) { setPreview(null); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    if (!job || job.status === 'failed' || result || pollingPaused) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const latest = await request<Job>(`/jobs/${job.job_id}`);
        if (cancelled) return;
        if (latest.status === 'completed') {
          const output = await request<Result>(`/jobs/${job.job_id}/result`);
          if (cancelled) return;
          setResult(output);
        }
        setJob(latest);
        setError(latest.error);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Could not retrieve job.');
          setPollingPaused(true);
        }
      } finally {
        if (!cancelled) setPollAttempt(n => n + 1);
      }
    }, 900);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [job, result, pollAttempt, pollingPaused]);

  async function submit() {
    if (!file) return;
    const validationError = validateVideo(file);
    if (validationError) { setError(validationError); return; }
    setPollingPaused(false);
    setBusy(true); setError(null); setResult(null); setJob(null);
    try {
      const data = new FormData(); data.append('video', file);
      setJob(await request<Job>('/jobs', { method: 'POST', body: data }));
    } catch (err) { setError(err instanceof Error ? err.message : 'Upload failed.'); }
    finally { setBusy(false); }
  }
  const processing = busy || (!pollingPaused && (job?.status === 'queued' || job?.status === 'running'));
  function seek(timestamp: number) {
    if (resultPlayer.current) {
      resultPlayer.current.currentTime = timestamp;
      resultPlayer.current.focus();
    }
  }
  return <main>
    <nav><a className="brand" href="/">◉ MUSEFX</a><span className="badge">LOCAL MVP · MOCK ADAPTERS</span></nav>
    <header><p className="eyebrow">FROM MOTION TO EMOTION</p><h1>Give your video<br /><span>a sound of its own.</span></h1><p className="intro">Drop in a clip. Find the moment. Hear the difference.<br />A tiny playground for video-to-sound effects.</p></header>
    <section className="workspace">
      <div className="panel upload"><div className="section-title"><span>01 / SOURCE</span><span>MP4 · 60 SEC · 1080P · 100 MB</span></div>
        <label className="dropzone"><span className="upload-icon">↥</span><strong>{file ? file.name : 'Choose your video'}</strong><span>Click to browse an MP4 from your device</span><input type="file" accept="video/mp4,.mp4" disabled={!!processing} onChange={e => { const next = e.target.files?.[0] || null; setFile(next); setJob(null); setResult(null); setPollingPaused(false); setError(next ? validateVideo(next) : null); }} /></label>
        <button className="primary" disabled={!file || !!validateVideo(file) || !!processing} onClick={submit}>{busy ? 'Uploading…' : processing ? 'Creating sound…' : 'Generate sound effects ↗'}</button>
        <p className="note">This demo adds an 880 Hz test tone at a mock event. Your video and existing audio are preserved.</p>
      </div>
      <div className="panel process"><div className="section-title"><span>02 / PIPELINE</span><span className="live">● LOCAL</span></div><h2>{job?.status === 'completed' ? 'Your clip, with a little extra.' : 'A moment becomes a sound.'}</h2>
        <div role="status" aria-live="polite"><p className="status">{busy ? 'Uploading video' : job ? `${job.stage} · ${job.progress}%` : 'Ready when you are'}</p><progress value={job?.progress || 0} max={100} aria-label="Pipeline progress" /></div>
        <div className="steps">{['detecting', 'describing', 'generating', 'mixing', 'done'].map((stage, i) => <div key={stage} className={job && job.progress >= [15,40,55,75,100][i] ? 'active' : ''}><span>{String(i+1).padStart(2,'0')}</span>{stage}</div>)}</div>
        {error && <p className="error" role="alert">{error}</p>}
        {pollingPaused && <button className="retry" onClick={() => { setError(null); setPollingPaused(false); }}>Reconnect to job ↻</button>}
      </div>
    </section>
    <section className="players"><div className="panel"><div className="section-title"><span>ORIGINAL</span><span>BEFORE</span></div>{preview ? <video controls aria-label="Original video" src={preview} /> : <div className="empty">Your source video will appear here</div>}</div><div className="panel"><div className="section-title"><span>SOUND ON</span><span>AFTER</span></div>{result ? <video ref={resultPlayer} aria-label="Result video with sound effects" controls src={media(result.result_video_url)} /> : <div className="empty">A new soundtrack is one clip away</div>}</div></section>
    <section className="panel timeline"><div className="section-title"><span>03 / EVENT TIMELINE</span><span>{result ? `${result.events.events.length} EVENT` : 'WAITING FOR DETECTION'}</span></div>{result ? <><div className="track">{result.events.events.map(event => <button key={event.id} style={{left: `${event.t / result.events.video.duration * 100}%`}} title={`Seek to ${event.t.toFixed(2)}s`} aria-label={`Seek to ${event.prompt}`} onClick={() => { seek(event.t); }}>●</button>)}</div>{result.events.events.map(event => <button className="event" key={event.id} onClick={() => seek(event.t)} title={`${event.type}: ${event.prompt}`}><code>{event.t.toFixed(2)}s</code><strong>{event.prompt}</strong><span>{event.duration.toFixed(2)}s · {Math.round(event.confidence * 100)}% confidence</span></button>)}<div className="downloads"><a href={`${API}/jobs/${job?.job_id}/download/result.mp4`}>Download MP4 ↗</a><a href={`${API}/jobs/${job?.job_id}/download/events.json`}>Download events.json ↗</a></div></> : <p className="note">Detected moments will appear here. Click an event to jump to its sound.</p>}</section>
    <footer>MUSEFX <span>Built for experimentation. Powered by replaceable adapters.</span></footer>
  </main>;
}
