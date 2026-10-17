export type DevicePlatform = 'ANDROID' | 'IOS';
export type CameraStatus = 'IDLE' | 'STREAMING';
export type NetworkStatus = 'WIFI' | 'CELLULAR' | 'DISCONNECTED';
export type AppRole = 'CAMERA_DEVICE' | 'VIEWER';

export interface User {
  id: string;
  name: string;
  email: string;
  created_at: string;
}

export interface CameraDevice {
  id: string;
  user_id: string;
  device_name: string;
  device_uuid: string;
  platform: DevicePlatform;
  is_online: boolean;
  camera_status: CameraStatus;
  last_seen: string | null;
  battery_level: number | null;
  network_status: NetworkStatus | null;
  created_at: string;
  updated_at: string;
}

export interface CameraEvent {
  id: string;
  camera_device_id: string;
  event_type: string;
  event_metadata: Record<string, unknown> | null;
  timestamp: string;
  created_at: string;
  image_path: string | null;
}

export interface CameraVideo {
  id: string;
  camera_device_id: string;
  started_at: string;
  duration_seconds: number;
  created_at: string;
  video_path: string;
}
