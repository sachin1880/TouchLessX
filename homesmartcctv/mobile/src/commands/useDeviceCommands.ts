import { useEffect, useRef, useState } from 'react';

import { API_BASE_URL } from '../api/client';
import { acknowledgeCommand, pendingCommands, reportCommandResult } from '../api/commands';
import { authStorage } from '../storage/authStorage';
import type { DeviceCommand } from '../types';
import { CommandProcessor, type Executors, type ProcessedCommand } from './commandProcessor';

const POLL_INTERVAL_MS = 10000; // the fallback when the control socket is down
const PING_INTERVAL_MS = 25000;
const MAX_RECONNECT_DELAY_MS = 30000;

type ControlMessage = { type: 'command'; command: DeviceCommand } | { type: 'pong' };

/**
 * Camera side of device commands. Listens on the authenticated control socket
 * (/api/devices/{id}/control) for commands as they're created, and polls
 * /commands/pending as a fallback, so a dropped socket only adds delay - it never
 * loses a command. Commands are carried out one at a time by CommandProcessor.
 *
 * Separate from the signaling socket on purpose: signaling only exists while a viewer
 * is watching; commands must reach the camera whenever the app is running.
 */
export function useDeviceCommands(deviceId: string | null, executors: Executors) {
  const [lastProcessed, setLastProcessed] = useState<ProcessedCommand | null>(null);
  const [connected, setConnected] = useState(false);
  const executorsRef = useRef(executors);
  executorsRef.current = executors;

  useEffect(() => {
    if (!deviceId) return;
    let stopped = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectDelay = 1000;

    // The executors are read through the ref, so the screen can re-render (new callbacks)
    // without tearing down the socket or forgetting which commands already ran.
    const processor = new CommandProcessor(
      {
        acknowledge: (id) => acknowledgeCommand(deviceId, id),
        report: (id, report) => reportCommandResult(deviceId, id, report),
      },
      {
        REFRESH_STATUS: (c) => executorsRef.current.REFRESH_STATUS(c),
        SWITCH_CAMERA: (c) => executorsRef.current.SWITCH_CAMERA(c),
        CAPTURE_SNAPSHOT: (c) => executorsRef.current.CAPTURE_SNAPSHOT(c),
      },
      (processed) => {
        if (!stopped) setLastProcessed(processed);
      },
    );

    const poll = async () => {
      try {
        processor.submit(await pendingCommands(deviceId));
      } catch {
        // Offline or backend restarting - the next poll or the socket catches up.
      }
    };

    const connect = async () => {
      const token = await authStorage.getToken();
      if (stopped || !token) return;
      const wsBase = API_BASE_URL.replace(/^http/, 'ws').replace(/\/api$/, '');
      const ws = new WebSocket(`${wsBase}/api/devices/${deviceId}/control?token=${encodeURIComponent(token)}`);
      socket = ws;
      ws.onopen = () => {
        reconnectDelay = 1000;
        setConnected(true);
      };
      ws.onmessage = (event: WebSocketMessageEvent) => {
        try {
          const message = JSON.parse(event.data) as ControlMessage;
          if (message.type === 'command') processor.submit([message.command]);
        } catch {
          // Malformed message - ignore; polling still covers the command.
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (stopped) return;
        reconnectTimer = setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
      };
      ws.onerror = () => {
        // onclose follows and schedules the reconnect.
      };
    };

    connect();
    poll();
    const pollTimer = setInterval(poll, POLL_INTERVAL_MS);
    const pingTimer = setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping' }));
    }, PING_INTERVAL_MS);

    return () => {
      stopped = true;
      clearInterval(pollTimer);
      clearInterval(pingTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
      setConnected(false);
    };
  }, [deviceId]);

  return { lastProcessed, connected };
}
