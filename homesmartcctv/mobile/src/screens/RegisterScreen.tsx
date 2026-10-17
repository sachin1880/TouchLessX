import React, { useState } from 'react';
import { View, Text, StyleSheet, Alert, Pressable, KeyboardAvoidingView, Platform, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import TextField from '../components/TextField';
import PrimaryButton from '../components/PrimaryButton';
import { colors, spacing, typography } from '../theme';
import { registerUser, loginUser } from '../api/auth';
import { authStorage } from '../storage/authStorage';
import { ApiError } from '../api/client';

type Props = NativeStackScreenProps<RootStackParamList, 'Register'>;

export default function RegisterScreen({ route, navigation }: Props) {
  const { role } = route.params;
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);

  const handleRegister = async () => {
    if (!name || !email || !password) {
      Alert.alert('Missing information', 'Fill in all fields.');
      return;
    }
    if (password.length < 8) {
      Alert.alert('Weak password', 'Password must be at least 8 characters.');
      return;
    }
    setLoading(true);
    try {
      await registerUser(name.trim(), email.trim(), password);
      const { access_token } = await loginUser(email.trim(), password);
      await authStorage.setToken(access_token);
      navigation.reset({
        index: 0,
        routes: [{ name: role === 'CAMERA_DEVICE' ? 'CameraDevice' : 'ViewerDashboard' }],
      });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Could not reach the server.';
      Alert.alert('Registration failed', message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <SafeAreaView style={styles.screen}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.brandIcon}>
            <Icon name="person-add" size={26} color={colors.primary} />
          </View>
          <Text style={styles.brand}>HOME CAMERA</Text>
          <Text style={styles.title}>Create Account</Text>
          <Text style={styles.subtitle}>One account controls all your cameras and viewers.</Text>

          <View style={styles.form}>
            <TextField
              label="Name"
              icon="person-outline"
              value={name}
              onChangeText={setName}
              placeholder="Your name"
            />
            <TextField
              label="Email"
              icon="mail-outline"
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              keyboardType="email-address"
              placeholder="you@example.com"
            />
            <TextField
              label="Password"
              icon="lock-closed-outline"
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              placeholder="At least 8 characters"
            />
            <PrimaryButton label="Create Account" onPress={handleRegister} loading={loading} />
          </View>

          <Pressable onPress={() => navigation.navigate('Login', { role })}>
            <Text style={styles.link}>Already have an account? Log in</Text>
          </Pressable>
        </ScrollView>
      </SafeAreaView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, flexGrow: 1, justifyContent: 'center' },
  brandIcon: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: `${colors.primary}1F`,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.md,
  },
  brand: { ...typography.label, color: colors.textTertiary, marginBottom: spacing.sm },
  title: { ...typography.title, color: colors.textPrimary, marginBottom: spacing.xs },
  subtitle: { ...typography.body, color: colors.textSecondary, marginBottom: spacing.xl },
  form: { marginBottom: spacing.lg },
  link: { ...typography.body, color: colors.primary, textAlign: 'center' },
});
