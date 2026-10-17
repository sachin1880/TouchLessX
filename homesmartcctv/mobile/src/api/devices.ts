import { apiClient, API_BASE_URL, ApiError } from './client';
import { authStorage } from '../storage/authStorage';
import type { CameraDevice, CameraEvent, CameraStatus, CameraVideo, DevicePlatform, NetworkStatus } from '../types';

const UPLOAD_TIMEOUT_MS = 20000;

/**
 * Shared multipart upload with a timeout - without one, a stalled upload leaves whatever
 * "don't overlap with the next one" guard called it (face detection, video segment rotation)
 * stuck forever, permanently halting that background loop.
 */
async function uploadMultipart(path: string, formData: FormData): Promise<Response> {
  const token = await authStorage.getToken();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      body: formData,
      signal: controller.signal,
    });

    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail ?? detail;
      } catch {
        // No JSON body - keep the status text.
      }
      throw new ApiError(response.status, detail);
    }
    return response;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === 'AbortError') {
      throw new ApiError(0, 'Upload timed out. Check your connection and try again.');
    }
    throw new ApiError(0, 'Could not reach the server.');
  } finally {
    clearTimeout(timeout);
  }
}

export function registerDevice(
  deviceName: string,
  deviceUuid: string,
  platform: DevicePlatform,
): Promise<CameraDevice> {
  return apiClient.post<CameraDevice>('/devices/register', {
    device_name: deviceName,
    device_uuid: deviceUuid,
    platform,
  });
}

export function listDevices(): Promise<CameraDevice[]> {
  return apiClient.get<CameraDevice[]>('/devices');
}

export function getDevice(deviceId: string): Promise<CameraDevice> {
  return apiClient.get<CameraDevice>(`/devices/${deviceId}`);
}

export function sendHeartbeat(
  deviceId: string,
  data: { battery_level?: number; network_status?: NetworkStatus; camera_status?: CameraStatus },
): Promise<CameraDevice> {
  return apiClient.post<CameraDevice>(`/devices/${deviceId}/heartbeat`, data);
}

export function startCameraRemote(deviceId: string): Promise<CameraDevice> {
  return apiClient.post<CameraDevice>(`/devices/${deviceId}/camera/start`);
}

export function stopCameraRemote(deviceId: string): Promise<CameraDevice> {
  return apiClient.post<CameraDevice>(`/devices/${deviceId}/camera/stop`);
}

export function listEvents(deviceId: string, limit = 50): Promise<CameraEvent[]> {
  return apiClient.get<CameraEvent[]>(`/devices/${deviceId}/events?limit=${limit}`);
}

/**
 * Uploads a captured JPEG (a local file:// path from MediaCaptureModule).
 */
export async function uploadCapture(
  deviceId: string,
  localFileUri: string,
  eventType: 'MANUAL_CAPTURE' | 'PERSON_DETECTED' = 'MANUAL_CAPTURE',
): Promise<CameraEvent> {
  const formData = new FormData();
  formData.append('file', {
    uri: localFileUri.startsWith('file://') ? localFileUri : `file://${localFileUri}`,
    type: 'image/jpeg',
    name: 'capture.jpg',
  } as unknown as Blob);
  formData.append('event_type', eventType);

  const response = await uploadMultipart(`/devices/${deviceId}/captures`, formData);
  return (await response.json()) as CameraEvent;
}

export function captureImageUrl(deviceId: string, eventId: string): string {
  return `${API_BASE_URL}/devices/${deviceId}/captures/${eventId}/image`;
}

/**
 * Uploads a finished ~60s recording segment (a local file:// path from VideoRecordingModule).
 */
export async function uploadVideo(
  deviceId: string,
  localFileUri: string,
  startedAt: Date,
  durationSeconds: number,
): Promise<CameraVideo> {
  const formData = new FormData();
  formData.append('file', {
    uri: localFileUri.startsWith('file://') ? localFileUri : `file://${localFileUri}`,
    type: 'video/mp4',
    name: 'segment.mp4',
  } as unknown as Blob);
  formData.append('started_at', startedAt.toISOString());
  formData.append('duration_seconds', String(durationSeconds));

  const response = await uploadMultipart(`/devices/${deviceId}/videos`, formData);
  return (await response.json()) as CameraVideo;
}

export function listVideos(deviceId: string, limit = 50): Promise<CameraVideo[]> {
  return apiClient.get<CameraVideo[]>(`/devices/${deviceId}/videos?limit=${limit}`);
}

export function videoFileUrl(deviceId: string, videoId: string): string {
  return `${API_BASE_URL}/devices/${deviceId}/videos/${videoId}/file`;
}
