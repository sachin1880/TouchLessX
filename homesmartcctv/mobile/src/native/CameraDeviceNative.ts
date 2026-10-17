import { NativeModules, Platform } from 'react-native';

export interface CameraDeviceModuleType {
  startForegroundNotification(): Promise<boolean>;
  stopForegroundNotification(): Promise<boolean>;
  getUptimeMillis(): Promise<number>;
  getStableDeviceId(): Promise<string>;
}

const noopModule: CameraDeviceModuleType = {
  startForegroundNotification: async () => false,
  stopForegroundNotification: async () => true,
  getUptimeMillis: async () => 0,
  getStableDeviceId: async () => 'unknown-device',
};

const CameraDeviceModule: CameraDeviceModuleType =
  Platform.OS === 'android' && NativeModules.CameraDeviceModule ? NativeModules.CameraDeviceModule : noopModule;

export default CameraDeviceModule;
