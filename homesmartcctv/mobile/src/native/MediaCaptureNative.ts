import { NativeModules, Platform } from 'react-native';

export interface MediaCaptureModuleType {
  /** Screenshots whatever video surface is inside the given native view, returns a local file:// path to the saved JPEG. */
  captureView(viewTag: number): Promise<string>;
}

const noopModule: MediaCaptureModuleType = {
  captureView: async () => {
    throw new Error('Capture is only available on Android.');
  },
};

const MediaCaptureModule: MediaCaptureModuleType =
  Platform.OS === 'android' && NativeModules.MediaCaptureModule ? NativeModules.MediaCaptureModule : noopModule;

export default MediaCaptureModule;
