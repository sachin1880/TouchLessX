export type SignalingRole = 'camera' | 'viewer';

export type SignalingMessage =
  | { type: 'peer-joined'; role: SignalingRole }
  | { type: 'peer-left'; role: SignalingRole }
  | { type: 'replaced' }
  | { type: 'offer'; sdp: string }
  | { type: 'answer'; sdp: string }
  | { type: 'ice-candidate'; candidate: string; sdpMid: string | null; sdpMLineIndex: number | null }
  | { type: 'switch-camera' };
