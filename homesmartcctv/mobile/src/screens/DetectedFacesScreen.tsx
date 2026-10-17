import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, StyleSheet, FlatList, RefreshControl, Image, Pressable, Modal } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import { colors, spacing, typography, radii } from '../theme';
import { captureImageUrl, listEvents } from '../api/devices';
import { authStorage } from '../storage/authStorage';
import type { CameraEvent } from '../types';

type Props = NativeStackScreenProps<RootStackParamList, 'DetectedFaces'>;

const GRID_GAP = 3;
const COLUMNS = 3;

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

/**
 * Just the PERSON_DETECTED events, separate from the mixed Event History - a photo grid
 * of "who showed up on camera" rather than a scroll of start/stop/battery noise.
 */
export default function DetectedFacesScreen({ route }: Props) {
  const { deviceId, deviceName } = route.params;
  const [faces, setFaces] = useState<CameraEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [selected, setSelected] = useState<CameraEvent | null>(null);

  useEffect(() => {
    authStorage.getToken().then(setAuthToken);
  }, []);

  const fetchFaces = useCallback(
    async (showSpinner = false) => {
      if (showSpinner) setRefreshing(true);
      try {
        const events = await listEvents(deviceId, 200);
        setFaces(events.filter((event) => event.event_type === 'PERSON_DETECTED'));
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
    fetchFaces();
  }, [fetchFaces]);

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.brand}>HOME CAMERA</Text>
        <Text style={styles.title}>Detected Faces</Text>
        <Text style={styles.subtitle}>{deviceName} · kept for 3 days</Text>
      </View>

      <FlatList
        data={faces}
        keyExtractor={(item) => item.id}
        numColumns={COLUMNS}
        contentContainerStyle={styles.grid}
        columnWrapperStyle={styles.gridRow}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => fetchFaces(true)} tintColor={colors.primary} />
        }
        ListEmptyComponent={
          !loading ? (
            <View style={styles.emptyState}>
              <Icon name="person-outline" size={32} color={colors.textTertiary} />
              <Text style={styles.emptyText}>
                No faces detected yet. While the camera is streaming, it automatically saves a photo here whenever
                someone appears on camera.
              </Text>
            </View>
          ) : undefined
        }
        renderItem={({ item }) =>
          authToken ? (
            <Pressable style={styles.thumbWrap} onPress={() => setSelected(item)}>
              <Image
                source={{ uri: captureImageUrl(deviceId, item.id), headers: { Authorization: `Bearer ${authToken}` } }}
                style={styles.thumb}
              />
            </Pressable>
          ) : null
        }
      />

      <Modal visible={!!selected} transparent animationType="fade" onRequestClose={() => setSelected(null)}>
        <View style={styles.viewerBackdrop}>
          <SafeAreaView style={styles.viewerSafeArea}>
            <Pressable style={styles.viewerClose} onPress={() => setSelected(null)} hitSlop={12}>
              <Icon name="close" size={26} color={colors.textPrimary} />
            </Pressable>
            {selected && authToken && (
              <>
                <Image
                  source={{
                    uri: captureImageUrl(deviceId, selected.id),
                    headers: { Authorization: `Bearer ${authToken}` },
                  }}
                  style={styles.viewerImage}
                  resizeMode="contain"
                />
                <Text style={styles.viewerTimestamp}>{formatTimestamp(selected.timestamp)}</Text>
              </>
            )}
          </SafeAreaView>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  header: { padding: spacing.lg, paddingBottom: spacing.sm },
  brand: { ...typography.label, color: colors.textTertiary, marginBottom: spacing.xs },
  title: { ...typography.title, fontSize: 22, color: colors.textPrimary },
  subtitle: { ...typography.body, fontSize: 13, color: colors.textTertiary, marginTop: 2 },
  grid: { paddingHorizontal: spacing.lg, paddingBottom: spacing.lg, gap: GRID_GAP, flexGrow: 1 },
  gridRow: { gap: GRID_GAP },
  thumbWrap: { flex: 1 / COLUMNS, aspectRatio: 1 },
  thumb: { width: '100%', height: '100%', borderRadius: radii.sm, backgroundColor: colors.surfaceElevated },
  emptyState: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.md, paddingTop: spacing.xxl },
  emptyText: {
    ...typography.body,
    color: colors.textSecondary,
    lineHeight: 20,
    textAlign: 'center',
    paddingHorizontal: spacing.lg,
  },
  viewerBackdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.92)' },
  viewerSafeArea: { flex: 1 },
  viewerClose: {
    alignSelf: 'flex-end',
    margin: spacing.md,
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.1)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  viewerImage: { flex: 1, width: '100%' },
  viewerTimestamp: {
    ...typography.body,
    color: colors.textSecondary,
    textAlign: 'center',
    paddingVertical: spacing.md,
  },
});
