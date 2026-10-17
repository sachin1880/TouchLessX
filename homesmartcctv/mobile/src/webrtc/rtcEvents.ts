import type { RTCPeerConnection } from 'react-native-webrtc';

export interface IceCandidateLike {
  candidate: string;
  sdpMid: string | null;
  sdpMLineIndex: number | null;
}

export interface RemoteStreamLike {
  toURL(): string;
}

/**
 * react-native-webrtc's RTCPeerConnection extends an EventTarget whose generic
 * `addEventListener(type, listener)` overload doesn't survive this project's module
 * resolution: @react-native/typescript-config's customConditions resolve every RN
 * library through its package.json "react-native" field, and this library's field
 * points at a mixed .ts/.js source tree where the EventTarget base ships as a .js+.d.ts
 * pair that loses that overload. This is a type-checking gap only - Metro bundles and
 * runs the real code fine either way. These narrow wrappers keep our own callback
 * bodies fully type-checked without depending on that broken inference.
 */
function addListener<TEvent>(pc: RTCPeerConnection, type: string, handler: (event: TEvent) => void): void {
  (pc as unknown as { addEventListener: (type: string, callback: (event: TEvent) => void) => void }).addEventListener(
    type,
    handler,
  );
}

export function onIceCandidate(pc: RTCPeerConnection, handler: (candidate: IceCandidateLike | null) => void): void {
  addListener<{ candidate: IceCandidateLike | null }>(pc, 'icecandidate', (event) => handler(event.candidate));
}

export function onTrack(pc: RTCPeerConnection, handler: (streams: RemoteStreamLike[]) => void): void {
  addListener<{ streams: RemoteStreamLike[] }>(pc, 'track', (event) => handler(event.streams));
}

export function onConnectionStateChange(pc: RTCPeerConnection, handler: () => void): void {
  addListener<unknown>(pc, 'connectionstatechange', () => handler());
}
