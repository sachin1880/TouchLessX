import { useCallback, useEffect, useRef, useState } from 'react';
import { RTCIceCandidate, RTCPeerConnection, RTCSessionDescription } from 'react-native-webrtc';

import { getIceServers } from '../api/webrtc';
import { onConnectionStateChange, onIceCandidate, onTrack } from './rtcEvents';
import { SignalingClient } from './signalingClient';
import type { SignalingMessage } from './types';

export type ViewerConnectionStatus =
  | 'waiting-for-camera'
  | 'connecting'
  | 'connected'
  | 'disconnected'
  | 'failed'
  | 'replaced';

/**
 * Viewer-side ("offerer") WebRTC lifecycle. Waits for the backend to report the camera
 * peer as present (either already there, or joining later), then negotiates a
 * receive-only offer for both audio and video.
 */
export function useViewerPeerConnection(deviceId: string) {
  const [remoteStreamURL, setRemoteStreamURL] = useState<string | null>(null);
  const [status, setStatus] = useState<ViewerConnectionStatus>('waiting-for-camera');
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const signalingRef = useRef<SignalingClient | null>(null);

  const closePeerConnection = useCallback(() => {
    pcRef.current?.close();
    pcRef.current = null;
    setRemoteStreamURL(null);
  }, []);

  const createOfferForCamera = useCallback(async () => {
    const signaling = signalingRef.current;
    if (!signaling) return;

    const { ice_servers } = await getIceServers();
    const pc = new RTCPeerConnection({ iceServers: ice_servers });
    pcRef.current = pc;

    pc.addTransceiver('video', { direction: 'recvonly' });
    pc.addTransceiver('audio', { direction: 'recvonly' });

    onIceCandidate(pc, (candidate) => {
      if (candidate) {
        signaling.send({ type: 'ice-candidate', ...candidate });
      }
    });

    onTrack(pc, (streams) => {
      if (streams[0]) {
        setRemoteStreamURL(streams[0].toURL());
      }
    });

    onConnectionStateChange(pc, () => {
      if (pc.connectionState === 'connected') setStatus('connected');
      else if (pc.connectionState === 'failed') setStatus('failed');
      else if (pc.connectionState === 'disconnected') setStatus('disconnected');
    });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    signaling.send({ type: 'offer', sdp: offer.sdp });
  }, []);

  useEffect(() => {
    let cancelled = false;
    const signaling = new SignalingClient();
    signalingRef.current = signaling;

    signaling.onMessage(async (message: SignalingMessage) => {
      if (cancelled) return;

      if (message.type === 'peer-joined' && message.role === 'camera') {
        setStatus('connecting');
        await createOfferForCamera();
      } else if (message.type === 'answer') {
        await pcRef.current?.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: message.sdp }));
      } else if (message.type === 'ice-candidate') {
        await pcRef.current?.addIceCandidate(
          new RTCIceCandidate({
            candidate: message.candidate,
            sdpMid: message.sdpMid,
            sdpMLineIndex: message.sdpMLineIndex,
          }),
        );
      } else if (message.type === 'peer-left' && message.role === 'camera') {
        closePeerConnection();
        setStatus('waiting-for-camera');
      } else if (message.type === 'replaced') {
        closePeerConnection();
        setStatus('replaced');
      }
    });

    signaling.onClose(() => {
      if (!cancelled) setStatus((current) => (current === 'replaced' ? current : 'disconnected'));
    });

    signaling.connect(deviceId, 'viewer').catch(() => {
      if (!cancelled) setStatus('failed');
    });

    return () => {
      cancelled = true;
      closePeerConnection();
      signaling.close();
    };
  }, [deviceId, createOfferForCamera, closePeerConnection]);

  const switchCamera = useCallback(() => {
    signalingRef.current?.send({ type: 'switch-camera' });
  }, []);

  return { remoteStreamURL, status, switchCamera };
}
