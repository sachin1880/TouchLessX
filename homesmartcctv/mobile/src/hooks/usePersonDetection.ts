import { useEffect, useRef } from 'react';
import FaceDetection from '@react-native-ml-kit/face-detection';

import MediaCaptureModule from '../native/MediaCaptureNative';
import { uploadCapture } from '../api/devices';

const CHECK_INTERVAL_MS = 10000;
// Once a person is captured, wait this long before capturing again - otherwise someone
// standing in frame would fill Event History with a near-identical photo every 10 seconds.
const CAPTURE_COOLDOWN_MS = 2 * 60 * 1000;
// PixelCopy's native callback can occasionally never fire (seen on real devices after a
// backgrounding/screen-lock cycle disrupts the view's render surface). Without a timeout,
// one stuck capture leaves `runningRef` permanently true and silently kills the detection
// loop for the rest of the session - a real failure mode, not a hypothetical one.
const CAPTURE_TIMEOUT_MS = 5000;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timed out')), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

/**
 * Polls the camera's own local preview for a face every CHECK_INTERVAL_MS while streaming,
 * using the same PixelCopy capture path as manual capture (MediaCaptureModule) plus an
 * on-device ML Kit face detector - no cloud calls, no continuous recording, just a
 * periodic still-frame check. On a hit, uploads the frame as a PERSON_DETECTED event so it
 * shows up in Event History like any other capture.
 */
export function usePersonDetection(deviceId: string | null, enabled: boolean, getViewTag: () => number | null) {
  const lastCaptureAtRef = useRef(0);
  const runningRef = useRef(false);

  useEffect(() => {
    if (!enabled || !deviceId) return;

    const interval = setInterval(async () => {
      if (runningRef.current) return;
      const tag = getViewTag();
      if (!tag) return;

      runningRef.current = true;
      try {
        const fileUri = await withTimeout(MediaCaptureModule.captureView(tag), CAPTURE_TIMEOUT_MS);
        // MediaCaptureModule resolves to a bare filesystem path (no scheme) - ML Kit's
        // InputImage.fromFilePath needs a proper file:// URI to resolve it via
        // ContentResolver, or it fails with "No content provider".
        const fileUrl = fileUri.startsWith('file://') ? fileUri : `file://${fileUri}`;
        const faces = await withTimeout(
          FaceDetection.detect(fileUrl, { performanceMode: 'fast' }),
          CAPTURE_TIMEOUT_MS,
        );
        const now = Date.now();
        if (faces.length > 0 && now - lastCaptureAtRef.current > CAPTURE_COOLDOWN_MS) {
          lastCaptureAtRef.current = now;
          await uploadCapture(deviceId, fileUri, 'PERSON_DETECTED');
        }
      } catch {
        // Best-effort background check - a failed frame grab or detection pass just
        // waits for the next tick rather than surfacing an error to the user.
      } finally {
        runningRef.current = false;
      }
    }, CHECK_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [deviceId, enabled, getViewTag]);
}
