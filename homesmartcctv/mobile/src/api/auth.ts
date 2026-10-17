import { apiClient } from './client';
import type { User } from '../types';

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export function registerUser(name: string, email: string, password: string): Promise<User> {
  return apiClient.post<User>('/auth/register', { name, email, password }, false);
}

export function loginUser(email: string, password: string): Promise<TokenResponse> {
  return apiClient.post<TokenResponse>('/auth/login', { email, password }, false);
}

export function getCurrentUser(): Promise<User> {
  return apiClient.get<User>('/users/me');
}
