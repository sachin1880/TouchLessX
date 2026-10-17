import { useCallback, useRef, useState } from 'react';
import {
  MediaStream,
  RTCIceCandidate,
  RTCPeerConnection,
  RTCSessionDescription,
  mediaDevices,
} from 'react-native-webrtc';

import { getIceServers } from '../api/webrtc';
import { onIceCandidate } from './rtcEvents';
import { SignalingClient } from './signalingClient';
import type { SignalingMessage } from './types';

/**
 * Camera-side ("answerer") WebRTC lifecycle: capture local media, connect signaling,
 * and answer whatever offer a viewer sends. One viewer at a time, matching the
 * backend's single-viewer-per-device signaling room.
 */
export function useCameraPeerConnection() {
  const [localStreamURL, setLocalStreamURL] = useState<string | null>(null);
  const [lensFacing, setLensFacing] = useState<'front' | 'back'>('back');
  const [videoTrackId, setVideoTrackId] = useState<string | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const signalingRef = useRef<SignalingClient | null>(null);

  const closePeerConnection = useCallback(() => {
    pcRef.current?.close();
    pcRef.current = null;
  }, []);

  /**
   * Switches the live video between the front and back camera (or to `facing`), keeping
   * the same track, so a connected viewer just sees the picture change. Used by the
   * viewer's flip button (signaling) and by the SWITCH_CAMERA device command.
   * Returns the camera now in use, or null when nothing is streaming.
   */
  const switchCamera = useCallback(async (facing?: 'front' | 'back'): Promise<'front' | 'back' | null> => {
    const videoTrack = streamRef.current?.getVideoTracks()[0];
    if (!videoTrack) return null;
    const current = videoTrack.getSettings().facingMode === 'user' ? 'front' : 'back';
    const next = facing ?? (current === 'front' ? 'back' : 'front');
    if (next !== current) {
      await videoTrack.applyConstraints({ facingMode: next === 'front' ? 'user' : 'environment' });
    }
    setLensFacing(next);
    return next;
  }, []);

  const handleSignalingMessage = useCallback(
    async (message: SignalingMessage) => {
      const stream = streamRef.current;
      const signaling = signalingRef.current;
      if (!stream || !signaling) return;

      if (message.type === 'offer') {
        closePeerConnection();
        const { ice_servers } = await getIceServers();
        const pc = new RTCPeerConnection({ iceServers: ice_servers });
        pcRef.current = pc;

        stream.getTracks().forEach((track) => pc.addTrack(track, stream));

        onIceCandidate(pc, (candidate) => {
          if (candidate) {
            signaling.send({ type: 'ice-candidate', ...candidate });
          }
        });

        await pc.setRemoteDescription(new RTCSessionDescription({ type: 'offer', sdp: message.sdp }));
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        signaling.send({ type: 'answer', sdp: answer.sdp });
      } else if (message.type === 'ice-candidate') {
        await pcRef.current?.addIceCandidate(
          new RTCIceCandidate({
            candidate: message.candidate,
            sdpMid: message.sdpMid,
            sdpMLineIndex: message.sdpMLineIndex,
          }),
        );
      } else if (message.type === 'peer-left' || message.type === 'replaced') {
        closePeerConnection();
      } else if (message.type === 'switch-camera') {
        await switchCamera();
      }
    },
    [closePeerConnection, switchCamera],
  );

  const start = useCallback(
    async (deviceId: string, initialLensFacing: 'front' | 'back') => {
      const stream = await mediaDevices.getUserMedia({
        audio: true,
        video: { facingMode: initialLensFacing === 'front' ? 'user' : 'environment' },
      });
      streamRef.current = stream as unknown as MediaStream;
      setLocalStreamURL(stream.toURL());
      setLensFacing(initialLensFacing);
      setVideoTrackId(stream.getVideoTracks()[0]?.id ?? null);

      const signaling = new SignalingClient();
      signalingRef.current = signaling;
      signaling.onMessage(handleSignalingMessage);
      await signaling.connect(deviceId, 'camera');
    },
    [handleSignalingMessage],
  );

  const stop = useCallback(() => {
    closePeerConnection();
    signalingRef.current?.close();
    signalingRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setLocalStreamURL(null);
    setVideoTrackId(null);
  }, [closePeerConnection]);

  const getVideoTrackDimensions = useCallback(() => {
    const settings = streamRef.current?.getVideoTracks()[0]?.getSettings();
    return { width: settings?.width ?? 0, height: settings?.height ?? 0 };
  }, []);

  return { localStreamURL, lensFacing, videoTrackId, start, stop, getVideoTrackDimensions, switchCamera };
}
