import { apiClient } from './client';
import type { DeviceCommand } from '../types';

/** Device-command API (backend: app/api/routes/commands.py). */
export function listCommands(deviceId: string, limit = 10): Promise<DeviceCommand[]> {
  return apiClient.get<DeviceCommand[]>(`/devices/${deviceId}/commands?limit=${limit}`);
}

/** Camera side: commands not yet accepted. The server marks them delivered. */
export function pendingCommands(deviceId: string): Promise<DeviceCommand[]> {
  return apiClient.get<DeviceCommand[]>(`/devices/${deviceId}/commands/pending`);
}

export function acknowledgeCommand(deviceId: string, commandId: string): Promise<DeviceCommand> {
  return apiClient.post<DeviceCommand>(`/devices/${deviceId}/commands/${commandId}/ack`);
}

export function reportCommandResult(
  deviceId: string,
  commandId: string,
  report: { status: 'SUCCEEDED' | 'FAILED'; error_code?: string; result?: Record<string, unknown> },
): Promise<DeviceCommand> {
  return apiClient.post<DeviceCommand>(`/devices/${deviceId}/commands/${commandId}/result`, report);
}
