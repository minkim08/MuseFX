export type Job = {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  stage: 'detecting' | 'describing' | 'generating' | 'mixing' | 'done' | 'failed';
  progress: number;
  error: string | null;
};
export type SoundEvent = {
  id: string;
  t: number;
  duration: number;
  type: 'footstep' | 'impact' | 'hit' | 'door' | 'pickup' | 'ui' | 'whoosh' | 'other';
  prompt: string;
  volume: number;
  pan: number;
  confidence: number;
};
export type Result = {
  original_video_url: string;
  result_video_url: string;
  events_url: string;
  events: { video: { id: string; duration: number; fps: number }; events: SoundEvent[]; ambience?: { prompt: string; volume: number } };
};
export const API = (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000').replace(/\/$/, '');
export const media = (path: string) => `${API}${path}`;

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60_000);
  try {
    const response = await fetch(`${API}${path}`, { ...options, cache: 'no-store', signal: controller.signal });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(typeof data?.detail === 'string' ? data.detail : `Request failed (${response.status})`);
    }
    return await response.json();
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      throw new Error('The API request timed out. Check that the backend is running and try again.');
    }
    if (error instanceof TypeError) {
      throw new Error('Cannot reach the API. Check that the backend is running on ' + API + '.');
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export function validateVideo(file: File): string | null {
  if (!file.name.toLowerCase().endsWith('.mp4')) return 'Please choose an MP4 video.';
  if (file.size === 0) return 'The selected file is empty.';
  if (file.size > 100 * 1024 * 1024) return 'Video must be 100 MB or smaller.';
  return null;
}
