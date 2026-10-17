import { useEffect, useState } from 'react';
import NetInfo from '@react-native-community/netinfo';
import type { NetworkStatus } from '../types';

export function useNetworkStatus(): NetworkStatus {
  const [status, setStatus] = useState<NetworkStatus>('DISCONNECTED');

  useEffect(() => {
    const unsubscribe = NetInfo.addEventListener((state) => {
      if (!state.isConnected) {
        setStatus('DISCONNECTED');
      } else if (state.type === 'wifi') {
        setStatus('WIFI');
      } else if (state.type === 'cellular') {
        setStatus('CELLULAR');
      } else {
        setStatus('WIFI');
      }
    });
    return unsubscribe;
  }, []);

  return status;
}
