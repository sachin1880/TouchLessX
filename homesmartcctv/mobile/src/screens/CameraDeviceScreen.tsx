import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { View, Text, StyleSheet, Alert, ScrollView, Pressable, findNodeHandle } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import DeviceInfo from 'react-native-device-info';
import { RTCView } from 'react-native-webrtc';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import Card from '../components/Card';
import PrimaryButton from '../components/PrimaryButton';
import CameraDeviceModule from '../native/CameraDeviceNative';
import MediaCaptureModule from '../native/MediaCaptureNative';
import { colors, spacing, typography, radii } from '../theme';
import { registerDevice, sendHeartbeat, startCameraRemote, stopCameraRemote, uploadCapture } from '../api/devices';
import { ApiError } from '../api/client';
import { useNetworkStatus } from '../hooks/useNetworkStatus';
import { useCameraPeerConnection } from '../webrtc/useCameraPeerConnection';
import { usePersonDetection } from '../hooks/usePersonDetection';
import { useContinuousRecording } from '../hooks/useContinuousRecording';
import { useDeviceCommands } from '../commands/useDeviceCommands';
import { CommandFailure, type Executors } from '../commands/commandProcessor';
import {
  requestCameraPermission,
  requestMicrophonePermission,
  requestNotificationPermission,
  openBatteryOptimizationSettings,
} from '../utils/permissions';
import { formatTimeOfDay, formatUptime } from '../utils/formatters';
import type { CameraDevice as CameraDeviceModel, CameraStatus } from '../types';

type Props = NativeStackScreenProps<RootStackParamList, 'CameraDevice'>;

const HEARTBEAT_INTERVAL_MS = 25000;
const DEVICE_NAME = 'Home Camera';

export default function CameraDeviceScreen({ navigation }: Props) {
  const [device, setDevice] = useState<CameraDeviceModel | null>(null);
  const [selectedLensFacing, setSelectedLensFacing] = useState<'front' | 'back'>('back');
  const [cameraStatus, setCameraStatus] = useState<CameraStatus>('IDLE');
  const [uptimeMs, setUptimeMs] = useState(0);
  const [busy, setBusy] = useState(false);
  const [registering, setRegistering] = useState(true);
  const networkStatus = useNetworkStatus();
  const deviceIdRef = useRef<string | null>(null);
  const previewRef = useRef<React.ComponentRef<typeof RTCView>>(null);
  const {
    localStreamURL,
    lensFacing: liveLensFacing,
    videoTrackId,
    start: startPeer,
    stop: stopPeer,
    getVideoTrackDimensions,
    switchCamera,
  } = useCameraPeerConnection();

  usePersonDetection(
    device?.id ?? null,
    cameraStatus === 'STREAMING',
    useCallback(() => findNodeHandle(previewRef.current) ?? null, []),
  );

  // Paused: VideoFileRenderer (the WebRTC utility the recording pipeline is built on) turned
  // out to write raw, uncompressed frames rather than real compressed video - a 60s clip at
  // 720p came out to 100-200MB instead of a few MB. The capture mechanism itself (attaching
  // directly to the native video track, independent of screen state) is proven and works;
  // what's missing is a real MediaCodec/MediaMuxer H.264 encoder in VideoRecordingModule to
  // replace VideoFileRenderer. Flip this back to `cameraStatus === 'STREAMING'` once that's
  // built - everything else (backend storage/retention, the Recordings playlist and player
  // screens) is already in place and ready.
  useContinuousRecording(device?.id ?? null, false, videoTrackId, getVideoTrackDimensions);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const deviceUuid = await CameraDeviceModule.getStableDeviceId();
        const registered = await registerDevice(DEVICE_NAME, deviceUuid, 'ANDROID');
        if (cancelled) return;
        deviceIdRef.current = registered.id;

        if (registered.camera_status === 'STREAMING') {
          // The backend remembers this device as STREAMING from before the app was last
          // closed/restarted, but a fresh launch never actually reconnects the camera or
          // signaling - reconcile the backend rather than showing a "Stop Camera" UI that
          // looks live while no video is actually being sent.
          try {
            const reconciled = await stopCameraRemote(registered.id);
            setDevice(reconciled);
          } catch {
            setDevice(registered);
          }
          setCameraStatus('IDLE');
        } else {
          setDevice(registered);
          setCameraStatus(registered.camera_status);
        }
      } catch (error) {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) {
          // apiClient already cleared the stale token on the 401 - get the user back to
          // Login rather than leaving them stuck on a "Registering..." screen with a
          // misleading "backend unreachable" message.
          navigation.reset({ index: 0, routes: [{ name: 'Login', params: { role: 'CAMERA_DEVICE' } }] });
          return;
        }
        Alert.alert('Could not register device', 'Check that the backend is reachable and try again.');
      } finally {
        if (!cancelled) setRegistering(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [navigation]);

  const sendHeartbeatTick = useCallback(async () => {
    const deviceId = deviceIdRef.current;
    if (!deviceId) return;
    try {
      const batteryFraction = await DeviceInfo.getBatteryLevel();
      const uptime = await CameraDeviceModule.getUptimeMillis();
      setUptimeMs(uptime);

      const updated = await sendHeartbeat(deviceId, {
        battery_level: Math.round(Math.max(0, batteryFraction) * 100),
        network_status: networkStatus,
        camera_status: cameraStatus,
      });
      setDevice(updated);
      return updated;
    } catch {
      // Expected during a network blip - the backend marks the device offline once the
      // missed-heartbeat window elapses, and we'll catch up on the next tick.
      return null;
    }
  }, [networkStatus, cameraStatus]);

  // Remote device commands (from TouchlessX or the viewer). Each executor reports what
  // actually happened - a command only succeeds if the camera really did it.
  const commandExecutors = useMemo<Executors>(
    () => ({
      REFRESH_STATUS: async () => {
        const updated = await sendHeartbeatTick();
        if (!updated) throw new CommandFailure('heartbeat_failed');
        return {
          battery_level: updated.battery_level,
          network_status: updated.network_status,
          camera_status: updated.camera_status,
        };
      },
      SWITCH_CAMERA: async (command) => {
        if (cameraStatus !== 'STREAMING') throw new CommandFailure('camera_not_streaming');
        const facing = command.parameters?.facing as 'front' | 'back' | undefined;
        const now = await switchCamera(facing);
        if (!now) throw new CommandFailure('camera_not_streaming');
        return { facing: now };
      },
      CAPTURE_SNAPSHOT: async () => {
        const deviceId = deviceIdRef.current;
        const tag = findNodeHandle(previewRef.current);
        if (cameraStatus !== 'STREAMING' || !tag || !deviceId) throw new CommandFailure('camera_not_streaming');
        const fileUri = await MediaCaptureModule.captureView(tag);
        const event = await uploadCapture(deviceId, fileUri, 'MANUAL_CAPTURE');
        return { event_id: event.id };
      },
    }),
    [cameraStatus, sendHeartbeatTick, switchCamera],
  );
  const { lastProcessed: lastCommand, connected: controlConnected } = useDeviceCommands(
    device?.id ?? null,
    commandExecutors,
  );

  useEffect(() => {
    // Gated on `registering` (flips once) and `deviceIdRef`, never on `device` itself -
    // every heartbeat response replaces `device` with a new object, so depending on it
    // here would restart this effect on every tick and fire heartbeats in a tight loop.
    if (registering || !deviceIdRef.current) return;
    sendHeartbeatTick();
    const interval = setInterval(sendHeartbeatTick, HEARTBEAT_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [registering, sendHeartbeatTick]);

  const handleStart = async () => {
    setBusy(true);
    try {
      const hasCameraPermission = await requestCameraPermission();
      if (!hasCameraPermission) {
        Alert.alert(
          'Camera permission required',
          'Home Camera cannot capture video without camera access. Enable it from Android Settings to continue.',
        );
        return;
      }
      const hasMicPermission = await requestMicrophonePermission();
      if (!hasMicPermission) {
        Alert.alert(
          'Microphone permission required',
          'Home Camera includes audio in the live stream. Enable microphone access from Android Settings to continue.',
        );
        return;
      }
      await requestNotificationPermission();

      const deviceId = deviceIdRef.current;
      if (!deviceId) return;

      await startPeer(deviceId, selectedLensFacing);
      await CameraDeviceModule.startForegroundNotification();
      setCameraStatus('STREAMING');

      try {
        const updated = await startCameraRemote(deviceId);
        setDevice(updated);
      } catch {
        // Local camera and signaling are already running regardless of this call's success.
      }
    } catch {
      Alert.alert('Could not start camera', 'The camera may be in use by another app.');
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    try {
      stopPeer();
      await CameraDeviceModule.stopForegroundNotification();
      setCameraStatus('IDLE');

      const deviceId = deviceIdRef.current;
      if (deviceId) {
        try {
          const updated = await stopCameraRemote(deviceId);
          setDevice(updated);
        } catch {
          // Best-effort - local camera is already stopped.
        }
      }
    } finally {
      setBusy(false);
    }
  };

  const isStreaming = cameraStatus === 'STREAMING';
  const isOnline = device?.is_online ?? false;
  const displayedLensFacing = isStreaming ? liveLensFacing : selectedLensFacing;

  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <View>
            <Text style={styles.brand}>HOME CAMERA</Text>
            <Text style={styles.deviceName}>{device?.device_name ?? 'Registering...'}</Text>
          </View>
          <View style={styles.headerRight}>
            <Pressable
              style={styles.profileButton}
              onPress={() => navigation.navigate('Profile', { role: 'CAMERA_DEVICE' })}
              hitSlop={12}
            >
              <Icon name="person-circle-outline" size={26} color={colors.textSecondary} />
            </Pressable>
          </View>
        </View>

        <View style={styles.heroTile}>
          {localStreamURL ? (
            <>
              <RTCView
                ref={previewRef}
                streamURL={localStreamURL}
                style={styles.previewSurface}
                objectFit="cover"
                mirror={displayedLensFacing === 'front'}
              />
              <View style={styles.heroTopRow}>
                <View style={styles.liveBadge}>
                  <View style={styles.liveDot} />
                  <Text style={styles.liveBadgeText}>LIVE</Text>
                </View>
              </View>
              <View style={styles.lensBadge}>
                <Icon name="camera-reverse-outline" size={16} color="rgba(255,255,255,0.85)" />
                <Text style={styles.lensBadgeText}>{displayedLensFacing === 'back' ? 'Back' : 'Front'}</Text>
              </View>
            </>
          ) : (
            <>
              <View style={styles.heroRingOuter} />
              <View style={styles.heroRingInner}>
                <Icon name="videocam-off-outline" size={30} color={colors.textTertiary} />
              </View>
              <View style={styles.heroTopRow}>
                <View style={[styles.heroStatusPill, isOnline ? styles.heroStatusPillOnline : styles.heroStatusPillOffline]}>
                  <View style={[styles.heroStatusDot, isOnline ? styles.heroStatusDotOnline : styles.heroStatusDotOffline]} />
                  <Text style={[styles.heroStatusText, isOnline ? styles.heroStatusTextOnline : styles.heroStatusTextOffline]}>
                    {isOnline ? 'Online' : 'Offline'}
                  </Text>
                </View>
              </View>
              <View style={styles.heroScrim} pointerEvents="none" />
              <View style={styles.heroBottomRow}>
                <Text style={styles.heroName}>{device?.device_name ?? 'Registering...'}</Text>
                <Text style={styles.heroMeta}>Camera is idle</Text>
              </View>
            </>
          )}
        </View>

        <View style={styles.statsRow}>
          <Stat
            icon="battery-half-outline"
            label="Battery"
            value={device?.battery_level != null ? `${device.battery_level}%` : '--'}
          />
          <View style={styles.statDivider} />
          <Stat icon="wifi-outline" label="Network" value={networkStatus} />
          <View style={styles.statDivider} />
          <Stat icon="time-outline" label="Uptime" value={formatUptime(uptimeMs)} />
        </View>

        <Card style={styles.infoCard}>
          <InfoRow
            icon="finger-print-outline"
            label="Device ID"
            value={device?.id ? device.id.slice(0, 8).toUpperCase() : '--------'}
            mono
          />
          <InfoRow
            icon="pulse-outline"
            label="Last Heartbeat"
            value={formatTimeOfDay(device?.last_seen ?? null)}
          />
          <InfoRow
            icon="radio-outline"
            label="Remote Control"
            value={controlConnected ? 'Connected' : 'Reconnecting...'}
          />
          <InfoRow icon="flash-outline" label="Last Command" value={describeCommand(lastCommand)} last />
        </Card>

        <Pressable
          style={styles.lensToggle}
          disabled={isStreaming}
          onPress={() => setSelectedLensFacing((prev) => (prev === 'back' ? 'front' : 'back'))}
        >
          <Text style={[styles.lensToggleText, isStreaming && styles.lensToggleTextDisabled]}>
            {isStreaming
              ? 'Camera can be switched remotely from the Viewer'
              : `Switch to ${selectedLensFacing === 'back' ? 'Front' : 'Back'} Camera`}
          </Text>
        </Pressable>

        <View style={styles.actions}>
          {!isStreaming ? (
            <PrimaryButton
              icon="play"
              label="Start Camera"
              onPress={handleStart}
              loading={busy}
              disabled={registering}
            />
          ) : (
            <PrimaryButton icon="stop" label="Stop Camera" variant="danger" onPress={handleStop} loading={busy} />
          )}
        </View>

        <Pressable style={styles.batteryTip} onPress={openBatteryOptimizationSettings}>
          <Icon name="battery-charging-outline" size={16} color={colors.textTertiary} />
          <Text style={styles.batteryTipText}>
            Keeping this offline or the app closing on its own? Turn off battery optimization for Home Camera.
          </Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

function Stat({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Icon name={icon} size={16} color={colors.textTertiary} />
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const COMMAND_NAMES: Record<string, string> = {
  REFRESH_STATUS: 'Camera Status',
  SWITCH_CAMERA: 'Flip Camera',
  CAPTURE_SNAPSHOT: 'Take Snapshot',
};

function describeCommand(command: { type: string; outcome: string; errorCode?: string; at: number } | null): string {
  if (!command) return 'None yet';
  const name = COMMAND_NAMES[command.type] ?? command.type;
  const time = formatTimeOfDay(new Date(command.at).toISOString());
  if (command.outcome === 'succeeded') return `${name} ✓ ${time}`;
  return `${name} ✗ ${command.errorCode ?? command.outcome} ${time}`;
}

function InfoRow({
  icon,
  label,
  value,
  mono,
  last,
}: {
  icon: string;
  label: string;
  value: string;
  mono?: boolean;
  last?: boolean;
}) {
  return (
    <View style={[styles.infoRow, !last && styles.infoRowDivider]}>
      <View style={styles.infoLabelRow}>
        <Icon name={icon} size={16} color={colors.textTertiary} />
        <Text style={styles.infoLabel}>{label}</Text>
      </View>
      <Text style={[styles.infoValue, mono && styles.infoValueMono]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, gap: spacing.md },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  headerRight: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  profileButton: { padding: 2 },
  brand: { ...typography.label, color: colors.textTertiary, marginBottom: spacing.xs },
  deviceName: { ...typography.title, fontSize: 22, color: colors.textPrimary },

  heroTile: {
    aspectRatio: 16 / 10,
    backgroundColor: colors.surfaceElevated,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    overflow: 'hidden',
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.3,
    shadowRadius: 16,
    elevation: 4,
  },
  previewSurface: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 },

  heroRingOuter: {
    position: 'absolute',
    width: 150,
    height: 150,
    borderRadius: 75,
    backgroundColor: 'rgba(61,155,255,0.06)',
  },
  heroRingInner: {
    width: 84,
    height: 84,
    borderRadius: 42,
    backgroundColor: 'rgba(61,155,255,0.12)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  heroTopRow: { position: 'absolute', top: spacing.md, right: spacing.md },
  heroStatusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 6,
    paddingHorizontal: spacing.sm,
    borderRadius: radii.pill,
  },
  heroStatusPillOnline: { backgroundColor: 'rgba(49,212,136,0.18)' },
  heroStatusPillOffline: { backgroundColor: 'rgba(255,90,95,0.18)' },
  heroStatusDot: { width: 7, height: 7, borderRadius: 4, marginRight: 6 },
  heroStatusDotOnline: { backgroundColor: colors.online },
  heroStatusDotOffline: { backgroundColor: colors.offline },
  heroStatusText: { ...typography.label, fontSize: 11 },
  heroStatusTextOnline: { color: colors.online },
  heroStatusTextOffline: { color: colors.offline },
  heroScrim: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    height: '45%',
    backgroundColor: 'rgba(5,7,10,0.5)',
  },
  heroBottomRow: { position: 'absolute', left: spacing.md, right: spacing.md, bottom: spacing.md },
  heroName: { ...typography.title, fontSize: 18, color: '#fff' },
  heroMeta: { ...typography.body, fontSize: 13, color: 'rgba(255,255,255,0.75)', marginTop: 2 },

  liveBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,90,95,0.85)',
    paddingVertical: 5,
    paddingHorizontal: spacing.sm,
    borderRadius: radii.pill,
  },
  liveDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#fff', marginRight: 6 },
  liveBadgeText: { ...typography.label, fontSize: 10, color: '#fff', letterSpacing: 1 },
  lensBadge: {
    position: 'absolute',
    left: spacing.md,
    bottom: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(10,14,20,0.55)',
    paddingVertical: 6,
    paddingHorizontal: spacing.sm,
    borderRadius: radii.pill,
  },
  lensBadgeText: { ...typography.label, fontSize: 11, color: 'rgba(255,255,255,0.85)' },

  statsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surfaceElevated,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  statDivider: { width: 1, height: 28, backgroundColor: colors.border },
  stat: { alignItems: 'center', flex: 1, gap: 2 },
  statValue: { ...typography.subtitle, color: colors.textPrimary, marginTop: 2 },
  statLabel: { ...typography.label, color: colors.textTertiary },

  infoCard: { gap: 0, padding: spacing.lg },
  infoRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: spacing.sm },
  infoRowDivider: { borderBottomWidth: 1, borderBottomColor: colors.border },
  infoLabelRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  infoLabel: { ...typography.body, color: colors.textSecondary },
  infoValue: { ...typography.subtitle, color: colors.textPrimary },
  infoValueMono: { fontFamily: 'monospace', fontSize: 14 },
  lensToggle: { alignSelf: 'center', padding: spacing.sm },
  lensToggleText: { ...typography.body, color: colors.primary },
  lensToggleTextDisabled: { color: colors.textTertiary },
  actions: { marginTop: spacing.sm },
  batteryTip: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    marginTop: spacing.md,
    padding: spacing.md,
    backgroundColor: colors.surfaceElevated,
    borderRadius: radii.md,
  },
  batteryTipText: { ...typography.body, color: colors.textTertiary, fontSize: 12, lineHeight: 16, flex: 1 },
});
