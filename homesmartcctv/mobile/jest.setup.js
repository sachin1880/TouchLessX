jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest'),
);

jest.mock('@react-native-community/netinfo', () => require('@react-native-community/netinfo/jest/netinfo-mock.js'));

jest.mock('react-native-device-info', () =>
  require('react-native-device-info/jest/react-native-device-info-mock.js'),
);

// react-native-webrtc reaches for a real native module at import time (to wire up its
// event emitter), which doesn't exist under Jest. There's no official mock, so this is a
// minimal stand-in covering only what this app actually imports from the package.
jest.mock('react-native-webrtc', () => ({
  mediaDevices: { getUserMedia: jest.fn().mockResolvedValue({ getTracks: () => [], toURL: () => 'mock://stream' }) },
  MediaStream: class MediaStream {},
  RTCPeerConnection: class RTCPeerConnection {
    addTrack() {}
    addTransceiver() {}
    addEventListener() {}
    createOffer() {
      return Promise.resolve({ sdp: 'mock-sdp', type: 'offer' });
    }
    createAnswer() {
      return Promise.resolve({ sdp: 'mock-sdp', type: 'answer' });
    }
    setLocalDescription() {
      return Promise.resolve();
    }
    setRemoteDescription() {
      return Promise.resolve();
    }
    addIceCandidate() {
      return Promise.resolve();
    }
    close() {}
  },
  RTCIceCandidate: class RTCIceCandidate {},
  RTCSessionDescription: class RTCSessionDescription {},
  RTCView: 'RTCView',
}));
