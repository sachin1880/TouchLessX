import type { AppRole } from '../types';

export type RootStackParamList = {
  RoleSelect: undefined;
  Login: { role: AppRole };
  Register: { role: AppRole };
  CameraDevice: undefined;
  ViewerDashboard: undefined;
  LiveCamera: { deviceId: string; deviceName: string };
  EventHistory: { deviceId: string; deviceName: string };
  CommandActivity: { deviceId: string; deviceName: string };
  DetectedFaces: { deviceId: string; deviceName: string };
  VideoPlaylist: { deviceId: string; deviceName: string };
  VideoPlayer: { deviceId: string; videoId: string; deviceName: string };
  CameraSettings: { deviceId: string; deviceName: string };
  Profile: { role: AppRole };
};
