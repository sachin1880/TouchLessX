import { Linking, PermissionsAndroid, Platform } from 'react-native';

/**
 * Requests CAMERA access. Required because the core purpose of this app mode is capturing
 * video - there is no way to function as a camera device without it. Requested only when
 * the user presses "Start Camera", never eagerly on launch.
 */
export async function requestCameraPermission(): Promise<boolean> {
  if (Platform.OS !== 'android') return true;

  const result = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.CAMERA, {
    title: 'Camera Permission',
    message:
      'Home Camera needs access to the camera to capture your live video feed. Without it the app cannot ' +
      'function as a camera device.',
    buttonPositive: 'Allow',
    buttonNegative: 'Deny',
  });
  return result === PermissionsAndroid.RESULTS.GRANTED;
}

/**
 * Requests RECORD_AUDIO. The live stream includes an audio track alongside video (a security
 * camera that can't be heard misses a lot), so this is requested together with camera access
 * when the user presses "Start Camera" - never eagerly on launch.
 */
export async function requestMicrophonePermission(): Promise<boolean> {
  if (Platform.OS !== 'android') return true;

  const result = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO, {
    title: 'Microphone Permission',
    message: 'Home Camera includes audio in your live stream so you can hear as well as see what is happening.',
    buttonPositive: 'Allow',
    buttonNegative: 'Deny',
  });
  return result === PermissionsAndroid.RESULTS.GRANTED;
}

/**
 * Requests POST_NOTIFICATIONS (Android 13+ only). Required so the persistent "camera is
 * active" notification can be shown, which is what keeps the foreground service - and the
 * camera - running while the screen is off. Purely informational, not asked for on older
 * Android versions where no runtime prompt exists.
 */
export async function requestNotificationPermission(): Promise<boolean> {
  if (Platform.OS !== 'android' || Platform.Version < 33) return true;

  const result = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS, {
    title: 'Notification Permission',
    message:
      'Home Camera shows a persistent notification while the camera is running so Android keeps it active ' +
      'in the background.',
    buttonPositive: 'Allow',
    buttonNegative: 'Deny',
  });
  return result === PermissionsAndroid.RESULTS.GRANTED;
}

/**
 * Opens Android's battery-optimization settings list. Many OEMs (especially on older/budget
 * phones - exactly the kind of phone this app is designed to repurpose as a camera) kill
 * background apps aggressively regardless of a correctly configured foreground service, so
 * there's no runtime permission for this - only a user-driven settings toggle. There's no
 * reliable way to detect whether it's already granted across OEMs, so this is offered as a
 * one-time nudge rather than gated behind a permission check.
 */
export function openBatteryOptimizationSettings(): void {
  if (Platform.OS !== 'android') return;
  Linking.sendIntent('android.settings.IGNORE_BATTERY_OPTIMIZATION_SETTINGS').catch(() => {
    Linking.openSettings().catch(() => undefined);
  });
}
