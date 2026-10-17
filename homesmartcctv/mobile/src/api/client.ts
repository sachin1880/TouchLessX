import { Platform } from 'react-native';
import DeviceInfo from 'react-native-device-info';
import { authStorage } from '../storage/authStorage';

// Your dev machine's LAN IP, for a real device testing on the same Wi-Fi network.
// Update this if your machine's IP changes (e.g. new network, DHCP renewal).
const DEV_MACHINE_LAN_IP = '192.168.29.191';

function resolveDevBaseUrl(): string {
  if (Platform.OS !== 'android') return 'http://localhost:8000/api';
  // 10.0.2.2 is the Android emulator's special alias for the host machine's localhost -
  // it doesn't work on a real device, which needs the host's actual LAN IP instead.
  return DeviceInfo.isEmulatorSync() ? 'http://10.0.2.2:8000/api' : `http://${DEV_MACHINE_LAN_IP}:8000/api`;
}

export const API_BASE_URL = resolveDevBaseUrl();

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const REQUEST_TIMEOUT_MS = 15000;

async function request<T>(path: string, options: RequestInit = {}, authenticated = true): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  };

  if (authenticated) {
    const token = await authStorage.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  // A dropped/stalled connection (flaky Wi-Fi, backend restart mid-request) can otherwise
  // leave fetch() pending forever, stranding the UI on a loading spinner with no way out.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers, signal: controller.signal });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      throw new ApiError(0, 'Request timed out. Check your connection and try again.');
    }
    throw new ApiError(0, 'Could not reach the server.');
  } finally {
    clearTimeout(timeout);
  }

  if (response.status === 401) {
    await authStorage.clearToken();
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // Response had no JSON body - keep the status text.
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const apiClient = {
  get: <T>(path: string) => request<T>(path, { method: 'GET' }),
  post: <T>(path: string, body?: unknown, authenticated = true) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }, authenticated),
};
