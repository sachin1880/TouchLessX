import React from 'react';
import { TextInput, View, Text, StyleSheet, TextInputProps } from 'react-native';
import Icon from 'react-native-vector-icons/Ionicons';
import { colors, radii, spacing, typography } from '../theme';

interface Props extends TextInputProps {
  label: string;
  icon?: string;
}

export default function TextField({ label, icon, style, ...rest }: Props) {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.inputRow}>
        {icon && <Icon name={icon} size={18} color={colors.textTertiary} style={styles.icon} />}
        <TextInput
          placeholderTextColor={colors.textTertiary}
          style={[styles.input, icon ? styles.inputWithIcon : null, style]}
          {...rest}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginBottom: spacing.md },
  label: { ...typography.label, color: colors.textSecondary, marginBottom: spacing.sm },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surfaceElevated,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  icon: { marginLeft: spacing.md },
  input: {
    flex: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    color: colors.textPrimary,
    fontSize: 15,
  },
  inputWithIcon: { paddingLeft: spacing.sm },
});
