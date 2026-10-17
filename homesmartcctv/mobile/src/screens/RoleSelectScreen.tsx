import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import Card from '../components/Card';
import PrimaryButton from '../components/PrimaryButton';
import { colors, spacing, typography } from '../theme';
import { appRoleStorage, authStorage } from '../storage/authStorage';
import type { AppRole } from '../types';

type Props = NativeStackScreenProps<RootStackParamList, 'RoleSelect'>;

export default function RoleSelectScreen({ navigation }: Props) {
  const choose = async (role: AppRole) => {
    await appRoleStorage.setRole(role);
    // Already signed in (e.g. switching roles on the same phone) - reuse the session
    // instead of making them type their email and password again.
    const token = await authStorage.getToken();
    if (token) {
      navigation.reset({
        index: 0,
        routes: [{ name: role === 'CAMERA_DEVICE' ? 'CameraDevice' : 'ViewerDashboard' }],
      });
      return;
    }
    navigation.replace('Login', { role });
  };

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.header}>
        <View style={styles.brandIcon}>
          <Icon name="shield-checkmark" size={28} color={colors.primary} />
        </View>
        <Text style={styles.brand}>HOME CAMERA</Text>
        <Text style={styles.title}>What is this device?</Text>
      </View>

      <View style={styles.cards}>
        <Card style={styles.card}>
          <View style={[styles.cardIcon, { backgroundColor: `${colors.primary}26` }]}>
            <Icon name="videocam" size={22} color={colors.primary} />
          </View>
          <Text style={styles.cardTitle}>Camera Device</Text>
          <Text style={styles.cardBody}>
            This phone stays at home and captures video. Choose this on the old phone you&apos;re repurposing
            as a security camera.
          </Text>
          <PrimaryButton icon="videocam" label="Set Up as Camera" onPress={() => choose('CAMERA_DEVICE')} />
        </Card>

        <Card style={styles.card}>
          <View style={[styles.cardIcon, { backgroundColor: `${colors.textSecondary}26` }]}>
            <Icon name="phone-portrait" size={22} color={colors.textSecondary} />
          </View>
          <Text style={styles.cardTitle}>Viewer</Text>
          <Text style={styles.cardBody}>
            Use this phone to watch your camera remotely, check its status, and review its event history.
          </Text>
          <PrimaryButton
            icon="eye"
            label="Set Up as Viewer"
            variant="secondary"
            onPress={() => choose('VIEWER')}
          />
        </Card>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background, padding: spacing.lg, justifyContent: 'center' },
  header: { marginBottom: spacing.xl, alignItems: 'center' },
  brandIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: `${colors.primary}1F`,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.md,
  },
  brand: { ...typography.label, color: colors.textTertiary, marginBottom: spacing.sm },
  title: { ...typography.title, color: colors.textPrimary, textAlign: 'center' },
  cards: { gap: spacing.md },
  card: { gap: spacing.sm },
  cardIcon: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.xs,
  },
  cardTitle: { ...typography.subtitle, fontSize: 18, color: colors.textPrimary },
  cardBody: { ...typography.body, color: colors.textSecondary, lineHeight: 20, marginBottom: spacing.xs },
});
