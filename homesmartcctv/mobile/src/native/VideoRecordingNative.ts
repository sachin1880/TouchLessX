import { NativeModules, Platform } from 'react-native';

export interface VideoRecordingModuleType {
  /**
   * Attaches a recorder directly to the native WebRTC video track (by its `MediaStreamTrack.id`)
   * and starts writing frames to a new MP4 under the app's cache dir - independent of whether
   * anything is currently rendered on screen, unlike MediaCaptureModule's PixelCopy-based
   * capture. Resolves with the absolute local file path once recording has started.
   */
  startSegment(trackId: string, fileName: string, width: number, height: number): Promise<string>;
  /** Detaches the recorder and finalizes the current segment's MP4 file. */
  stopSegment(): Promise<boolean>;
}

const noopModule: VideoRecordingModuleType = {
  startSegment: async () => {
    throw new Error('Video recording is only available on Android.');
  },
  stopSegment: async () => false,
};

const VideoRecordingModule: VideoRecordingModuleType =
  Platform.OS === 'android' && NativeModules.VideoRecordingModule
    ? NativeModules.VideoRecordingModule
    : noopModule;

export default VideoRecordingModule;
