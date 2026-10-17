import { API_BASE_URL } from '../api/client';
import { authStorage } from '../storage/authStorage';
import type { SignalingMessage, SignalingRole } from './types';

/**
 * Thin wrapper around RN's built-in WebSocket for the signaling channel. Not
 * auto-reconnecting by design - if the connection drops mid-call, the UI surfaces a
 * disconnected state and the user restarts the stream, which is simpler and more
 * predictable than resuming a half-negotiated peer connection.
 */
export class SignalingClient {
  private ws: WebSocket | null = null;
  private messageHandlers = new Set<(message: SignalingMessage) => void>();
  private closeHandlers = new Set<() => void>();

  async connect(deviceId: string, role: SignalingRole): Promise<void> {
    const token = await authStorage.getToken();
    if (!token) throw new Error('Not authenticated');

    const wsBase = API_BASE_URL.replace(/^http/, 'ws').replace(/\/api$/, '');
    const url = `${wsBase}/api/devices/${deviceId}/signal?token=${encodeURIComponent(token)}&role=${role}`;

    return new Promise((resolve, reject) => {
      const socket = new WebSocket(url);
      this.ws = socket;
      let settled = false;

      socket.onopen = () => {
        settled = true;
        resolve();
      };
      socket.onerror = () => {
        if (!settled) {
          settled = true;
          reject(new Error('Signaling connection failed'));
        }
      };
      socket.onmessage = (event: WebSocketMessageEvent) => {
        try {
          const message = JSON.parse(event.data) as SignalingMessage;
          this.messageHandlers.forEach((handler) => handler(message));
        } catch {
          // Malformed message - ignore rather than crash the call.
        }
      };
      socket.onclose = () => {
        this.closeHandlers.forEach((handler) => handler());
      };
    });
  }

  send(message: SignalingMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  onMessage(handler: (message: SignalingMessage) => void): () => void {
    this.messageHandlers.add(handler);
    return () => this.messageHandlers.delete(handler);
  }

  onClose(handler: () => void): () => void {
    this.closeHandlers.add(handler);
    return () => this.closeHandlers.delete(handler);
  }

  close(): void {
    this.messageHandlers.clear();
    this.closeHandlers.clear();
    this.ws?.close();
    this.ws = null;
  }
}
