import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, StyleSheet, FlatList, RefreshControl, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import Card from '../components/Card';
import { colors, spacing, typography } from '../theme';
import { listVideos } from '../api/devices';
import type { CameraVideo } from '../types';

type Props = NativeStackScreenProps<RootStackParamList, 'VideoPlaylist'>;

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

/**
 * Every ~60s recording segment the camera has uploaded, newest first - a continuous relay
 * of short clips rather than one long recording (see useContinuousRecording), kept for
 * video_retention_days (2 by default) before the backend deletes them.
 */
export default function VideoPlaylistScreen({ route, navigation }: Props) {
  const { deviceId, deviceName } = route.params;
  const [videos, setVideos] = useState<CameraVideo[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchVideos = useCallback(
    async (showSpinner = false) => {
      if (showSpinner) setRefreshing(true);
      try {
        const result = await listVideos(deviceId, 200);
        setVideos(result);
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
    fetchVideos();
  }, [fetchVideos]);

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.brand}>HOME CAMERA</Text>
        <Text style={styles.title}>Recordings</Text>
        <Text style={styles.subtitle}>{deviceName} · kept for 2 days</Text>
      </View>

      <FlatList
        data={videos}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => fetchVideos(true)} tintColor={colors.primary} />
        }
        ListEmptyComponent={
          !loading ? (
            <View style={styles.emptyState}>
              <Icon name="videocam-outline" size={32} color={colors.textTertiary} />
              <Text style={styles.emptyText}>
                No recordings yet. While the camera is streaming, it continuously saves ~1 minute clips here.
              </Text>
            </View>
          ) : undefined
        }
        renderItem={({ item }) => (
          <Pressable
            onPress={() =>
              navigation.navigate('VideoPlayer', { deviceId, videoId: item.id, deviceName })
            }
          >
            <Card style={styles.row}>
              <View style={styles.iconBadge}>
                <Icon name="play" size={18} color={colors.primary} />
              </View>
              <View style={styles.rowBody}>
                <Text style={styles.rowTitle}>{formatTimestamp(item.started_at)}</Text>
                <Text style={styles.rowMeta}>{formatDuration(item.duration_seconds)}</Text>
              </View>
              <Icon name="chevron-forward" size={18} color={colors.textTertiary} />
            </Card>
          </Pressable>
        )}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  header: { padding: spacing.lg, paddingBottom: spacing.sm },
  brand: { ...typography.label, color: colors.textTertiary, marginBottom: spacing.xs },
  title: { ...typography.title, fontSize: 22, color: colors.textPrimary },
  subtitle: { ...typography.body, fontSize: 13, color: colors.textTertiary, marginTop: 2 },
  list: { paddingHorizontal: spacing.lg, paddingBottom: spacing.lg, gap: spacing.sm, flexGrow: 1 },
  emptyState: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.md, paddingTop: spacing.xxl },
  emptyText: {
    ...typography.body,
    color: colors.textSecondary,
    lineHeight: 20,
    textAlign: 'center',
    paddingHorizontal: spacing.lg,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, padding: spacing.md },
  iconBadge: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: `${colors.primary}26`,
  },
  rowBody: { flex: 1 },
  rowTitle: { ...typography.subtitle, fontSize: 15, color: colors.textPrimary },
  rowMeta: { ...typography.body, fontSize: 13, color: colors.textTertiary, marginTop: 2 },
});
