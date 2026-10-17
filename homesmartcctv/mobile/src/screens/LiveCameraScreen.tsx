import React, { useRef, useState } from 'react';
import { View, Text, StyleSheet, Pressable, findNodeHandle, ActivityIndicator, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { RTCView } from 'react-native-webrtc';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import { colors, spacing, typography, radii } from '../theme';
import { useViewerPeerConnection } from '../webrtc/useViewerPeerConnection';
import MediaCaptureModule from '../native/MediaCaptureNative';
import { uploadCapture } from '../api/devices';

type Props = NativeStackScreenProps<RootStackParamList, 'LiveCamera'>;

const STATUS_LABEL: Record<string, string> = {
  'waiting-for-camera': 'Waiting for camera to start streaming...',
  connecting: 'Connecting...',
  connected: 'Live',
  disconnected: 'Disconnected',
  failed: 'Connection failed',
  replaced: 'Opened on another device',
};

export default function LiveCameraScreen({ route, navigation }: Props) {
  const { deviceId, deviceName } = route.params;
  const { remoteStreamURL, status, switchCamera } = useViewerPeerConnection(deviceId);
  const videoRef = useRef<React.ComponentRef<typeof RTCView>>(null);
  const [capturing, setCapturing] = useState(false);

  const isConnected = status === 'connected' && !!remoteStreamURL;
  const showReconnect = status === 'failed' || status === 'disconnected';

  const handleCapture = async () => {
    const tag = findNodeHandle(videoRef.current);
    if (!tag) return;

    setCapturing(true);
    try {
      const localUri = await MediaCaptureModule.captureView(tag);
      await uploadCapture(deviceId, localUri);
      Alert.alert('Captured', 'Saved to Event History. It will be kept for 3 days.');
    } catch {
      Alert.alert('Capture failed', 'Could not save this moment. Try again.');
    } finally {
      setCapturing(false);
    }
  };

  return (
    <View style={styles.screen}>
      <View style={styles.videoBox}>
        {isConnected ? (
          <RTCView ref={videoRef} streamURL={remoteStreamURL} style={StyleSheet.absoluteFill} objectFit="cover" />
        ) : (
          <View style={styles.placeholder}>
            <Icon name="videocam-off-outline" size={32} color={colors.textTertiary} />
            <Text style={styles.placeholderText}>{STATUS_LABEL[status] ?? status}</Text>
            {showReconnect && (
              <Pressable
                style={styles.reconnectButton}
                onPress={() => navigation.replace('LiveCamera', { deviceId, deviceName })}
              >
                <Text style={styles.reconnectText}>Reconnect</Text>
              </Pressable>
            )}
          </View>
        )}
      </View>

      <SafeAreaView style={styles.overlay} pointerEvents="box-none">
        <View style={styles.topBar}>
          <Pressable onPress={() => navigation.goBack()} style={styles.roundButton} hitSlop={12}>
            <Icon name="chevron-back" size={24} color={colors.textPrimary} />
          </Pressable>

          <View style={styles.titleBadge}>
            <Text style={styles.title}>{deviceName}</Text>
            <View style={styles.statusRow}>
              <View style={[styles.statusDot, { backgroundColor: isConnected ? colors.online : colors.textTertiary }]} />
              <Text style={styles.statusText}>{STATUS_LABEL[status] ?? status}</Text>
            </View>
          </View>

          <View style={styles.roundButtonSpacer} />
        </View>

        <View style={styles.bottomBar}>
          <Pressable
            style={[styles.roundButton, styles.controlButton]}
            onPress={switchCamera}
            disabled={!isConnected}
            hitSlop={12}
          >
            <Icon name="camera-reverse-outline" size={24} color={isConnected ? colors.textPrimary : colors.textTertiary} />
          </Pressable>

          <Pressable
            style={[styles.captureButton, (!isConnected || capturing) && styles.captureButtonDisabled]}
            onPress={handleCapture}
            disabled={!isConnected || capturing}
          >
            {capturing ? <ActivityIndicator color={colors.background} /> : <View style={styles.captureButtonInner} />}
          </Pressable>

          <View style={[styles.roundButton, styles.controlButton, styles.hiddenButton]} />
        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#000' },
  videoBox: { flex: 1, backgroundColor: '#000' },
  placeholder: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.md, padding: spacing.lg },
  placeholderText: { ...typography.body, color: colors.textSecondary, textAlign: 'center' },
  reconnectButton: {
    backgroundColor: colors.primary,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: radii.md,
  },
  reconnectText: { ...typography.subtitle, color: colors.textPrimary },

  overlay: { ...StyleSheet.absoluteFill, justifyContent: 'space-between' },
  topBar: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
  },
  roundButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(10,14,20,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  roundButtonSpacer: { width: 40, height: 40 },
  titleBadge: {
    backgroundColor: 'rgba(10,14,20,0.55)',
    borderRadius: radii.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    maxWidth: '65%',
  },
  title: { ...typography.subtitle, fontSize: 15, color: colors.textPrimary },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, marginTop: 2 },
  statusDot: { width: 6, height: 6, borderRadius: 3 },
  statusText: { ...typography.label, fontSize: 11, color: colors.textSecondary },

  bottomBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xl,
    paddingBottom: spacing.lg,
  },
  controlButton: { width: 52, height: 52, borderRadius: 26 },
  hiddenButton: { opacity: 0 },
  captureButton: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: colors.textPrimary,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 4,
    borderColor: 'rgba(255,255,255,0.3)',
  },
  captureButtonDisabled: { opacity: 0.4 },
  captureButtonInner: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.background,
  },
});
