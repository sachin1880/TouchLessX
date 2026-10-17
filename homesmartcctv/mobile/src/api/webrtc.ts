import { apiClient } from './client';

export interface IceServer {
  urls: string;
}

export interface IceServersResponse {
  ice_servers: IceServer[];
}

export function getIceServers(): Promise<IceServersResponse> {
  return apiClient.get<IceServersResponse>('/webrtc/ice-servers');
}
