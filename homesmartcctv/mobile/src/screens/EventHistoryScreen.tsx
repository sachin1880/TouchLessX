import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, StyleSheet, FlatList, RefreshControl, Image } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import Card from '../components/Card';
import { colors, spacing, typography, radii } from '../theme';
import { captureImageUrl, listEvents } from '../api/devices';
import { authStorage } from '../storage/authStorage';
import type { CameraEvent } from '../types';

type Props = NativeStackScreenProps<RootStackParamList, 'EventHistory'>;

const EVENT_ICONS: Record<string, { name: string; color: string }> = {
  CAMERA_STARTED: { name: 'videocam', color: colors.online },
  CAMERA_STOPPED: { name: 'videocam-off-outline', color: colors.textTertiary },
  DEVICE_ONLINE: { name: 'radio-button-on', color: colors.online },
  DEVICE_OFFLINE: { name: 'radio-button-off', color: colors.offline },
  NETWORK_DISCONNECTED: { name: 'cloud-offline-outline', color: colors.warning },
  NETWORK_RECONNECTED: { name: 'cloud-done-outline', color: colors.online },
  LOW_BATTERY: { name: 'battery-dead-outline', color: colors.warning },
  MANUAL_CAPTURE: { name: 'camera', color: colors.primary },
  PERSON_DETECTED: { name: 'person', color: colors.warning },
};

const IMAGE_BACKED_EVENT_TYPES = ['MANUAL_CAPTURE', 'PERSON_DETECTED'];

function formatEventType(eventType: string): string {
  return eventType
    .toLowerCase()
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

export default function EventHistoryScreen({ route }: Props) {
  const { deviceId, deviceName } = route.params;
  const [events, setEvents] = useState<CameraEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [authToken, setAuthToken] = useState<string | null>(null);

  useEffect(() => {
    authStorage.getToken().then(setAuthToken);
  }, []);

  const fetchEvents = useCallback(
    async (showSpinner = false) => {
      if (showSpinner) setRefreshing(true);
      try {
        const result = await listEvents(deviceId);
        setEvents(result);
      } catch {
        // Keep showing the last known list on a transient error.
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [deviceId],
  );

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.brand}>HOME CAMERA</Text>
        <Text style={styles.title}>{deviceName} Events</Text>
      </View>

      <FlatList
        data={events}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => fetchEvents(true)} tintColor={colors.primary} />
        }
        ListEmptyComponent={
          !loading ? (
            <Card>
              <Text style={styles.emptyText}>
                No events yet. Events appear here as the camera starts, stops, goes offline, or you capture a
                moment while watching live.
              </Text>
            </Card>
          ) : undefined
        }
        renderItem={({ item }) => {
          const icon = EVENT_ICONS[item.event_type] ?? { name: 'ellipse-outline', color: colors.textTertiary };
          const isCapture = IMAGE_BACKED_EVENT_TYPES.includes(item.event_type) && !!item.image_path;

          return (
            <Card style={styles.eventCard}>
              <View style={[styles.iconBadge, { backgroundColor: `${icon.color}26` }]}>
                <Icon name={icon.name} size={18} color={icon.color} />
              </View>
              <View style={styles.eventBody}>
                <Text style={styles.eventType}>{formatEventType(item.event_type)}</Text>
                <Text style={styles.eventTime}>{formatTimestamp(item.timestamp)}</Text>
              </View>
              {isCapture && authToken && (
                <Image
                  source={{ uri: captureImageUrl(deviceId, item.id), headers: { Authorization: `Bearer ${authToken}` } }}
                  style={styles.thumbnail}
                />
              )}
            </Card>
          );
        }}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  header: { padding: spacing.lg, paddingBottom: spacing.sm },
  brand: { ...typography.label, color: colors.textTertiary, marginBottom: spacing.xs },
  title: { ...typography.title, fontSize: 22, color: colors.textPrimary },
  list: { paddingHorizontal: spacing.lg, paddingBottom: spacing.lg, gap: spacing.sm },
  emptyText: { ...typography.body, color: colors.textSecondary, lineHeight: 20 },
  eventCard: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, padding: spacing.md },
  iconBadge: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  eventBody: { flex: 1 },
  eventType: { ...typography.subtitle, fontSize: 15, color: colors.textPrimary },
  eventTime: { ...typography.body, fontSize: 13, color: colors.textTertiary, marginTop: 2 },
  thumbnail: { width: 48, height: 48, borderRadius: radii.sm, backgroundColor: colors.surfaceElevated },
});
