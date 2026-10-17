import React, { useEffect, useState } from 'react';
import { View, StyleSheet, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import Video from 'react-native-video';
import Icon from 'react-native-vector-icons/Ionicons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import type { RootStackParamList } from '../navigation/types';
import { colors } from '../theme';
import { videoFileUrl } from '../api/devices';
import { authStorage } from '../storage/authStorage';

type Props = NativeStackScreenProps<RootStackParamList, 'VideoPlayer'>;

export default function VideoPlayerScreen({ route, navigation }: Props) {
  const { deviceId, videoId } = route.params;
  const [authToken, setAuthToken] = useState<string | null>(null);

  useEffect(() => {
    authStorage.getToken().then(setAuthToken);
  }, []);

  return (
    <View style={styles.screen}>
      {authToken && (
        <Video
          source={{
            uri: videoFileUrl(deviceId, videoId),
            headers: { Authorization: `Bearer ${authToken}` },
          }}
          style={styles.video}
          controls
          resizeMode="contain"
          paused={false}
        />
      )}
      <SafeAreaView style={styles.overlay} pointerEvents="box-none">
        <Pressable style={styles.closeButton} onPress={() => navigation.goBack()} hitSlop={12}>
          <Icon name="close" size={24} color={colors.textPrimary} />
        </Pressable>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#000' },
  video: StyleSheet.absoluteFill,
  overlay: StyleSheet.absoluteFill,
  closeButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    margin: 12,
    backgroundColor: 'rgba(10,14,20,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
});
