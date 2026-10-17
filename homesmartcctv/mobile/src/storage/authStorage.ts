import AsyncStorage from '@react-native-async-storage/async-storage';
import type { AppRole } from '../types';

const TOKEN_KEY = 'auth_token';
const APP_ROLE_KEY = 'app_role';

export const authStorage = {
  getToken(): Promise<string | null> {
    return AsyncStorage.getItem(TOKEN_KEY);
  },
  setToken(token: string): Promise<void> {
    return AsyncStorage.setItem(TOKEN_KEY, token);
  },
  clearToken(): Promise<void> {
    return AsyncStorage.removeItem(TOKEN_KEY);
  },
};

export const appRoleStorage = {
  async getRole(): Promise<AppRole | null> {
    const value = await AsyncStorage.getItem(APP_ROLE_KEY);
    return value === 'CAMERA_DEVICE' || value === 'VIEWER' ? value : null;
  },
  setRole(role: AppRole): Promise<void> {
    return AsyncStorage.setItem(APP_ROLE_KEY, role);
  },
  clearRole(): Promise<void> {
    return AsyncStorage.removeItem(APP_ROLE_KEY);
  },
};
