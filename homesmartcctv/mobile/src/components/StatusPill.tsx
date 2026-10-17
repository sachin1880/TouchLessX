import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors, radii, spacing, typography } from '../theme';

interface Props {
  online: boolean;
  onlineLabel?: string;
  offlineLabel?: string;
}

export default function StatusPill({ online, onlineLabel = 'Online', offlineLabel = 'Offline' }: Props) {
  const tint = online ? colors.online : colors.offline;

  return (
    <View style={[styles.pill, online ? styles.pillOnline : styles.pillOffline]}>
      <View style={[styles.dot, { backgroundColor: tint }]} />
      <Text style={[styles.text, { color: tint }]}>{online ? onlineLabel : offlineLabel}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.md,
    borderRadius: radii.pill,
    alignSelf: 'flex-start',
  },
  pillOnline: { backgroundColor: 'rgba(49,212,136,0.15)' },
  pillOffline: { backgroundColor: 'rgba(255,90,95,0.15)' },
  dot: { width: 8, height: 8, borderRadius: 4, marginRight: spacing.sm },
  text: { ...typography.label },
});
