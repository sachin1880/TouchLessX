import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, StyleSheet, FlatList, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import Card from '../components/Card';
import { colors, spacing, typography } from '../theme';
import { listCommands } from '../api/commands';
import type { CommandStatus, DeviceCommand } from '../types';

type Props = NativeStackScreenProps<RootStackParamList, 'CommandActivity'>;

// Faster than the dashboard: a command usually settles within a few seconds.
const POLL_INTERVAL_MS = 3000;

const COMMAND_INFO: Record<string, { label: string; icon: string }> = {
  REFRESH_STATUS: { label: 'Camera Status', icon: 'refresh-outline' },
  SWITCH_CAMERA: { label: 'Flip Camera', icon: 'camera-reverse-outline' },
  CAPTURE_SNAPSHOT: { label: 'Take Snapshot', icon: 'camera-outline' },
};

// What each state means to the person watching - "succeeded" is only ever shown once
// the camera itself has reported that it did it.
const STATUS_INFO: Record<CommandStatus, { text: string; color: string }> = {
  PENDING: { text: 'Waiting for camera', color: colors.warning },
  DELIVERED: { text: 'Delivered', color: colors.warning },
  ACKNOWLEDGED: { text: 'Camera is doing it', color: colors.primary },
  SUCCEEDED: { text: 'Done', color: colors.online },
  FAILED: { text: 'Failed', color: colors.danger },
  EXPIRED: { text: "Camera didn't respond", color: colors.danger },
  CANCELLED: { text: 'Cancelled', color: colors.textTertiary },
};

const SOURCES: Record<string, string> = { touchlessx: 'TouchlessX (gesture / voice)', viewer: 'Viewer app' };

function formatTime(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'medium' });
}

function describeResult(command: DeviceCommand): string | null {
  if (command.status === 'FAILED' && command.error_code) return `Reason: ${command.error_code.replace(/_/g, ' ')}`;
  const result = command.result ?? {};
  if (command.command_type === 'SWITCH_CAMERA' && result.facing) return `Now using the ${result.facing} camera`;
  if (command.command_type === 'CAPTURE_SNAPSHOT' && result.event_id) return 'Saved to Event History';
  if (command.command_type === 'REFRESH_STATUS' && result.battery_level != null) {
    return `Battery ${result.battery_level}% · ${result.network_status ?? 'network unknown'}`;
  }
  return null;
}

export default function CommandActivityScreen({ route }: Props) {
  const { deviceId, deviceName } = route.params;
  const [commands, setCommands] = useState<DeviceCommand[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchCommands = useCallback(
    async (showSpinner = false) => {
      if (showSpinner) setRefreshing(true);
      try {
        setCommands(await listCommands(deviceId, 30));
      } catch {
        // Keep the last known list on a transient error; the next poll retries.
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [deviceId],
  );

  useEffect(() => {
    fetchCommands();
    const interval = setInterval(fetchCommands, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchCommands]);

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.brand}>HOME CAMERA</Text>
        <Text style={styles.title}>{deviceName} Commands</Text>
      </View>

      <FlatList
        data={commands}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => fetchCommands(true)} tintColor={colors.primary} />
        }
        ListEmptyComponent={
          !loading ? (
            <Card>
              <Text style={styles.emptyText}>
                No commands yet. Commands sent from TouchlessX - by gesture, voice or its Home CCTV page - appear
                here with what the camera actually did.
              </Text>
            </Card>
          ) : undefined
        }
        renderItem={({ item }) => {
          const info = COMMAND_INFO[item.command_type] ?? { label: item.command_type, icon: 'ellipse-outline' };
          const status = STATUS_INFO[item.status];
          const detail = describeResult(item);
          return (
            <Card style={styles.row}>
              <View style={[styles.iconBadge, { backgroundColor: `${status.color}26` }]}>
                <Icon name={info.icon} size={18} color={status.color} />
              </View>
              <View style={styles.body}>
                <Text style={styles.name}>{info.label}</Text>
                <Text style={styles.meta}>
                  {formatTime(item.created_at)} · {SOURCES[item.source ?? ''] ?? item.source ?? 'Unknown source'}
                </Text>
                {detail && <Text style={styles.meta}>{detail}</Text>}
              </View>
              <Text style={[styles.status, { color: status.color }]}>{status.text}</Text>
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
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, padding: spacing.md },
  iconBadge: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
  body: { flex: 1 },
  name: { ...typography.subtitle, fontSize: 15, color: colors.textPrimary },
  meta: { ...typography.body, fontSize: 12, color: colors.textTertiary, marginTop: 2 },
  status: { ...typography.label, maxWidth: 110, textAlign: 'right' },
});
