import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import Card from '../components/Card';
import StatusPill from '../components/StatusPill';
import { colors, spacing, typography } from '../theme';
import { getDevice } from '../api/devices';
import type { CameraDevice } from '../types';

type Props = NativeStackScreenProps<RootStackParamList, 'CameraSettings'>;

export default function CameraSettingsScreen({ route }: Props) {
  const { deviceId, deviceName } = route.params;
  const [device, setDevice] = useState<CameraDevice | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDevice(deviceId)
      .then((result) => {
        if (!cancelled) setDevice(result);
      })
      .catch(() => {
        // Keep showing the params-derived name if the fetch fails.
      });
    return () => {
      cancelled = true;
    };
  }, [deviceId]);

  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <Text style={styles.brand}>HOME CAMERA</Text>
          <Text style={styles.title}>{deviceName} Settings</Text>
        </View>

        <Card style={styles.card}>
          <InfoRow icon="pulse-outline" label="Status" value={<StatusPill online={device?.is_online ?? false} />} />
          <InfoRow
            icon="videocam-outline"
            label="Device Name"
            value={<Text style={styles.value}>{device?.device_name ?? deviceName}</Text>}
          />
          <InfoRow
            icon="phone-portrait-outline"
            label="Platform"
            value={<Text style={styles.value}>{device?.platform ?? '--'}</Text>}
          />
          <InfoRow
            icon="finger-print-outline"
            label="Device ID"
            value={<Text style={[styles.value, styles.mono]}>{device?.device_uuid ?? '--'}</Text>}
            last
          />
        </Card>

        <View style={styles.footnoteRow}>
          <Icon name="information-circle-outline" size={16} color={colors.textTertiary} />
          <Text style={styles.footnote}>
            More camera settings (resolution, motion sensitivity, renaming) are planned for a future version.
          </Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function InfoRow({ icon, label, value, last }: { icon: string; label: string; value: React.ReactNode; last?: boolean }) {
  return (
    <View style={[styles.infoRow, !last && styles.infoRowDivider]}>
      <View style={styles.labelRow}>
        <Icon name={icon} size={16} color={colors.textTertiary} />
        <Text style={styles.label}>{label}</Text>
      </View>
      {value}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, gap: spacing.md },
  header: { marginBottom: spacing.sm },
  brand: { ...typography.label, color: colors.textTertiary, marginBottom: spacing.xs },
  title: { ...typography.title, fontSize: 22, color: colors.textPrimary },
  card: { gap: 0 },
  infoRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: spacing.md },
  infoRowDivider: { borderBottomWidth: 1, borderBottomColor: colors.border },
  labelRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  label: { ...typography.body, color: colors.textSecondary },
  value: { ...typography.subtitle, color: colors.textPrimary },
  mono: { fontFamily: 'monospace', fontSize: 13 },
  footnoteRow: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  footnote: { ...typography.body, color: colors.textTertiary, fontSize: 13, lineHeight: 18, flex: 1 },
});
