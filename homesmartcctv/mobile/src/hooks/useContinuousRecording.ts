import { useEffect } from 'react';

import VideoRecordingModule from '../native/VideoRecordingNative';
import { uploadVideo } from '../api/devices';

const SEGMENT_DURATION_MS = 60 * 1000;

/**
 * Records the camera's stream to a rolling sequence of ~60s MP4 segments while `enabled`,
 * uploading each one to the backend as soon as it finishes and immediately starting the
 * next - there's never one long recording, just a continuous relay of short clips. Uses
 * VideoRecordingModule, which attaches directly to the native WebRTC video track rather
 * than screenshotting the rendered preview, so recording keeps working even if the phone's
 * screen locks.
 */
export function useContinuousRecording(
  deviceId: string | null,
  enabled: boolean,
  videoTrackId: string | null,
  getTrackDimensions: () => { width: number; height: number },
) {
  useEffect(() => {
    if (!enabled || !deviceId || !videoTrackId) return;

    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let currentOutputPath: string | null = null;
    let currentStartedAt: Date | null = null;

    const finishCurrentSegment = async () => {
      const path = currentOutputPath;
      const startedAt = currentStartedAt;
      currentOutputPath = null;
      currentStartedAt = null;
      if (!path || !startedAt) return;

      try {
        await VideoRecordingModule.stopSegment();
        const durationSeconds = Math.max(1, Math.round((Date.now() - startedAt.getTime()) / 1000));
        await uploadVideo(deviceId, path, startedAt, durationSeconds);
      } catch {
        // Best effort - this segment is lost, but the next one will still be attempted.
      }
    };

    const startNextSegment = async () => {
      if (cancelled) return;
      const fileName = `segment_${Date.now()}.mp4`;
      const startedAt = new Date();
      try {
        const { width, height } = getTrackDimensions();
        const path = await VideoRecordingModule.startSegment(
          videoTrackId,
          fileName,
          width || 1280,
          height || 720,
        );
        currentOutputPath = path;
        currentStartedAt = startedAt;
      } catch {
        // Couldn't start this segment - the next scheduled rotation will try again rather
        // than retrying in a tight loop.
      }
      if (!cancelled) {
        timeoutId = setTimeout(rotateSegment, SEGMENT_DURATION_MS);
      }
    };

    const rotateSegment = async () => {
      await finishCurrentSegment();
      await startNextSegment();
    };

    startNextSegment();

    return () => {
      cancelled = true;
      if (timeoutId) clearTimeout(timeoutId);
      finishCurrentSegment();
    };
  }, [deviceId, enabled, videoTrackId, getTrackDimensions]);
}
