import React, { useCallback, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, Pressable, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import Card from '../components/Card';
import PrimaryButton from '../components/PrimaryButton';
import { colors, spacing, typography, radii } from '../theme';
import { listDevices } from '../api/devices';
import { ApiError } from '../api/client';
import { formatTimeOfDay } from '../utils/formatters';
import type { CameraDevice } from '../types';

type Props = NativeStackScreenProps<RootStackParamList, 'ViewerDashboard'>;

const POLL_INTERVAL_MS = 10000;

export default function ViewerDashboardScreen({ navigation }: Props) {
  const [devices, setDevices] = useState<CameraDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchDevices = useCallback(
    async (showSpinner = false) => {
      if (showSpinner) setRefreshing(true);
      try {
        const result = await listDevices();
        setDevices(result);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          // apiClient already cleared the stale token - get the user back to Login rather
          // than leaving them stuck on a dashboard that will silently fail every poll.
          navigation.reset({ index: 0, routes: [{ name: 'Login', params: { role: 'VIEWER' } }] });
          return;
        }
        // Any other error: keep showing the last known state - a transient network error
        // shouldn't blank the screen.
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [navigation],
  );

  useFocusEffect(
    useCallback(() => {
      fetchDevices();
      const interval = setInterval(fetchDevices, POLL_INTERVAL_MS);
      return () => clearInterval(interval);
    }, [fetchDevices]),
  );

  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => fetchDevices(true)} tintColor={colors.primary} />
        }
      >
        <View style={styles.header}>
          <View>
            <Text style={styles.brand}>HOME CAMERA</Text>
            <Text style={styles.title}>Your Cameras</Text>
          </View>
          <Pressable
            style={styles.profileButton}
            onPress={() => navigation.navigate('Profile', { role: 'VIEWER' })}
            hitSlop={12}
          >
            <Icon name="person-circle-outline" size={28} color={colors.textSecondary} />
          </Pressable>
        </View>

        {!loading && devices.length === 0 && (
          <Card style={styles.emptyCard}>
            <Text style={styles.emptyTitle}>No camera registered yet</Text>
            <Text style={styles.emptyBody}>
              Set up the old phone as a Camera Device first (choose &quot;Camera Device&quot; on it and log in
              with this same account). It will appear here automatically.
            </Text>
          </Card>
        )}

        {devices.map((device) => (
          <DeviceCard
            key={device.id}
            device={device}
            onView={() => navigation.navigate('LiveCamera', { deviceId: device.id, deviceName: device.device_name })}
            onSettings={() =>
              navigation.navigate('CameraSettings', { deviceId: device.id, deviceName: device.device_name })
            }
            onHistory={() =>
              navigation.navigate('EventHistory', { deviceId: device.id, deviceName: device.device_name })
            }
            onFaces={() =>
              navigation.navigate('DetectedFaces', { deviceId: device.id, deviceName: device.device_name })
            }
            onVideos={() =>
              navigation.navigate('VideoPlaylist', { deviceId: device.id, deviceName: device.device_name })
            }
          />
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const NETWORK_ICONS: Record<string, string> = {
  WIFI: 'wifi',
  CELLULAR: 'cellular',
  DISCONNECTED: 'cloud-offline-outline',
};

function DeviceCard({
  device,
  onView,
  onSettings,
  onHistory,
  onFaces,
  onVideos,
}: {
  device: CameraDevice;
  onView: () => void;
  onSettings: () => void;
  onHistory: () => void;
  onFaces: () => void;
  onVideos: () => void;
}) {
  const online = device.is_online;

  return (
    <View style={styles.deviceCard}>
      <Pressable
        onPress={onView}
        disabled={!online}
        style={({ pressed }) => [styles.heroTile, pressed && online && styles.heroTilePressed]}
      >
        <View style={styles.heroRingOuter} />
        <View style={styles.heroRingInner}>
          <Icon name="videocam" size={34} color={online ? colors.primary : colors.textTertiary} />
        </View>

        <View style={styles.heroTopRow}>
          <View style={[styles.heroStatusPill, online ? styles.heroStatusPillOnline : styles.heroStatusPillOffline]}>
            <View style={[styles.heroStatusDot, online ? styles.heroStatusDotOnline : styles.heroStatusDotOffline]} />
            <Text style={[styles.heroStatusText, online ? styles.heroStatusTextOnline : styles.heroStatusTextOffline]}>
              {online ? 'Live' : 'Offline'}
            </Text>
          </View>
        </View>

        {online && (
          <View style={styles.heroPlayButton}>
            <Icon name="play" size={22} color={colors.textPrimary} />
          </View>
        )}

        <View style={styles.heroScrim} pointerEvents="none" />
        <View style={styles.heroBottomRow}>
          <Text style={styles.heroName}>{device.device_name}</Text>
          <Text style={styles.heroMeta}>
            {online ? 'Tap to watch live' : `Last seen ${formatTimeOfDay(device.last_seen)}`}
          </Text>
        </View>
      </Pressable>

      <View style={styles.statsRow}>
        <Stat icon="time-outline" label="Last Seen" value={formatTimeOfDay(device.last_seen)} />
        <View style={styles.statDivider} />
        <Stat
          icon="battery-half-outline"
          label="Battery"
          value={device.battery_level != null ? `${device.battery_level}%` : '--'}
        />
        <View style={styles.statDivider} />
        <Stat
          icon={NETWORK_ICONS[device.network_status ?? ''] ?? 'help-outline'}
          label="Network"
          value={device.network_status ?? '--'}
        />
      </View>

      <View style={styles.buttonRow}>
        <PrimaryButton
          icon="settings-outline"
          label="Settings"
          variant="secondary"
          onPress={onSettings}
          style={styles.buttonHalf}
        />
        <PrimaryButton
          icon="list-outline"
          label="Event History"
          variant="secondary"
          onPress={onHistory}
          style={styles.buttonHalf}
        />
      </View>
      <View style={styles.buttonRow}>
        <PrimaryButton
          icon="person-outline"
          label="Detected Faces"
          variant="secondary"
          onPress={onFaces}
          style={styles.buttonHalf}
        />
        <PrimaryButton
          icon="film-outline"
          label="Recordings"
          variant="secondary"
          onPress={onVideos}
          style={styles.buttonHalf}
        />
      </View>
    </View>
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

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, gap: spacing.md },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: spacing.sm,
  },
  profileButton: { padding: 2 },
  brand: { ...typography.label, color: colors.textTertiary, marginBottom: spacing.xs },
  title: { ...typography.title, color: colors.textPrimary },
  emptyCard: { gap: spacing.sm },
  emptyTitle: { ...typography.subtitle, fontSize: 17, color: colors.textPrimary },
  emptyBody: { ...typography.body, color: colors.textSecondary, lineHeight: 20 },

  deviceCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    overflow: 'hidden',
    gap: spacing.md,
    paddingBottom: spacing.lg,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.3,
    shadowRadius: 16,
    elevation: 4,
  },

  heroTile: {
    aspectRatio: 16 / 10,
    backgroundColor: colors.surfaceElevated,
    justifyContent: 'center',
    alignItems: 'center',
    overflow: 'hidden',
  },
  heroTilePressed: { opacity: 0.9 },
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
  heroTopRow: {
    position: 'absolute',
    top: spacing.md,
    right: spacing.md,
  },
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
  heroPlayButton: {
    position: 'absolute',
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: 'rgba(10,14,20,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.2)',
  },
  heroScrim: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    height: '45%',
    backgroundColor: 'rgba(5,7,10,0.5)',
  },
  heroBottomRow: {
    position: 'absolute',
    left: spacing.md,
    right: spacing.md,
    bottom: spacing.md,
  },
  heroName: { ...typography.title, fontSize: 20, color: '#fff' },
  heroMeta: { ...typography.body, fontSize: 13, color: 'rgba(255,255,255,0.75)', marginTop: 2 },

  statsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginHorizontal: spacing.lg,
    backgroundColor: colors.surfaceElevated,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  statDivider: { width: 1, height: 28, backgroundColor: colors.border },
  stat: { alignItems: 'center', flex: 1, gap: 2 },
  statValue: { ...typography.subtitle, color: colors.textPrimary, marginTop: 2 },
  statLabel: { ...typography.label, color: colors.textTertiary },
  buttonRow: { flexDirection: 'row', gap: spacing.sm, marginHorizontal: spacing.lg },
  buttonHalf: { flex: 1 },
});
