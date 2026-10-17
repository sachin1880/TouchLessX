import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, Pressable, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import Card from '../components/Card';
import { colors, spacing, typography, radii } from '../theme';
import { getCurrentUser } from '../api/auth';
import { authStorage, appRoleStorage } from '../storage/authStorage';
import { formatDate } from '../utils/formatters';
import type { User } from '../types';

type Props = NativeStackScreenProps<RootStackParamList, 'Profile'>;

export default function ProfileScreen({ route, navigation }: Props) {
  const { role } = route.params;
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser()
      .then((result) => {
        if (!cancelled) setUser(result);
      })
      .catch(() => {
        // Keep showing the loading state - the header still lets the user log out.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const initial = user?.name?.trim()?.charAt(0).toUpperCase() || '?';

  const handleSwitchRole = () => {
    Alert.alert(
      'Switch Role',
      'Use this phone as the other role instead? You’ll stay signed in to the same account.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Switch',
          onPress: async () => {
            await appRoleStorage.clearRole();
            navigation.reset({ index: 0, routes: [{ name: 'RoleSelect' }] });
          },
        },
      ],
    );
  };

  const handleLogout = () => {
    Alert.alert('Log Out', 'Are you sure you want to log out?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Log Out',
        style: 'destructive',
        onPress: async () => {
          await authStorage.clearToken();
          await appRoleStorage.clearRole();
          navigation.reset({ index: 0, routes: [{ name: 'RoleSelect' }] });
        },
      },
    ]);
  };

  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <Pressable onPress={() => navigation.goBack()} style={styles.roundButton} hitSlop={12}>
            <Icon name="chevron-back" size={22} color={colors.textPrimary} />
          </Pressable>
          <Text style={styles.headerTitle}>Profile</Text>
          <View style={styles.roundButton} />
        </View>

        <View style={styles.avatarBlock}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{initial}</Text>
          </View>
          <Text style={styles.name}>{user?.name ?? 'Loading...'}</Text>
          <Text style={styles.email}>{user?.email ?? ''}</Text>
        </View>

        <Card style={styles.card}>
          <InfoRow
            icon={role === 'CAMERA_DEVICE' ? 'videocam-outline' : 'eye-outline'}
            label="Role on this device"
            value={role === 'CAMERA_DEVICE' ? 'Camera Device' : 'Viewer'}
          />
          <InfoRow icon="calendar-outline" label="Member Since" value={formatDate(user?.created_at ?? null)} last />
        </Card>

        <View style={styles.footnoteRow}>
          <Icon name="information-circle-outline" size={16} color={colors.textTertiary} />
          <Text style={styles.footnote}>
            One account can be used as both a Camera Device and a Viewer. Use "Switch Role" below to change what
            this phone does without logging in again.
          </Text>
        </View>

        <Pressable onPress={handleSwitchRole} style={styles.switchRoleButton}>
          <Icon name="swap-horizontal-outline" size={18} color={colors.primary} />
          <Text style={styles.switchRoleText}>
            Switch to {role === 'CAMERA_DEVICE' ? 'Viewer' : 'Camera Device'}
          </Text>
        </Pressable>

        <Pressable onPress={handleLogout} style={styles.logoutButton}>
          <Icon name="log-out-outline" size={18} color={colors.danger} />
          <Text style={styles.logoutText}>Log Out</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

function InfoRow({ icon, label, value, last }: { icon: string; label: string; value: string; last?: boolean }) {
  return (
    <View style={[styles.infoRow, !last && styles.infoRowDivider]}>
      <View style={styles.labelRow}>
        <Icon name={icon} size={16} color={colors.textTertiary} />
        <Text style={styles.label}>{label}</Text>
      </View>
      <Text style={styles.value}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, gap: spacing.md },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  headerTitle: { ...typography.subtitle, fontSize: 17, color: colors.textPrimary },
  roundButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarBlock: { alignItems: 'center', marginTop: spacing.sm, marginBottom: spacing.sm },
  avatar: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: `${colors.primary}1F`,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.sm,
  },
  avatarText: { ...typography.title, fontSize: 28, color: colors.primary },
  name: { ...typography.title, fontSize: 20, color: colors.textPrimary },
  email: { ...typography.body, color: colors.textSecondary, marginTop: 2 },
  card: { gap: 0 },
  infoRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: spacing.md },
  infoRowDivider: { borderBottomWidth: 1, borderBottomColor: colors.border },
  labelRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  label: { ...typography.body, color: colors.textSecondary },
  value: { ...typography.subtitle, color: colors.textPrimary },
  footnoteRow: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  footnote: { ...typography.body, color: colors.textTertiary, fontSize: 13, lineHeight: 18, flex: 1 },
  switchRoleButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    marginTop: spacing.md,
    padding: spacing.md,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.primary,
  },
  switchRoleText: { ...typography.subtitle, color: colors.primary },
  logoutButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    marginTop: spacing.sm,
    padding: spacing.md,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.danger,
  },
  logoutText: { ...typography.subtitle, color: colors.danger },
});
