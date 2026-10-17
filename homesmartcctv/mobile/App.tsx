/**
 * Smart Home Camera
 * @format
 */

import React from 'react';
import { View, ActivityIndicator, StyleSheet, StatusBar } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { RootStackParamList } from './src/navigation/types';
import { useBootstrap } from './src/hooks/useBootstrap';
import { colors } from './src/theme';
import RoleSelectScreen from './src/screens/RoleSelectScreen';
import LoginScreen from './src/screens/LoginScreen';
import RegisterScreen from './src/screens/RegisterScreen';
import CameraDeviceScreen from './src/screens/CameraDeviceScreen';
import ViewerDashboardScreen from './src/screens/ViewerDashboardScreen';
import LiveCameraScreen from './src/screens/LiveCameraScreen';
import EventHistoryScreen from './src/screens/EventHistoryScreen';
import DetectedFacesScreen from './src/screens/DetectedFacesScreen';
import VideoPlaylistScreen from './src/screens/VideoPlaylistScreen';
import VideoPlayerScreen from './src/screens/VideoPlayerScreen';
import CameraSettingsScreen from './src/screens/CameraSettingsScreen';
import ProfileScreen from './src/screens/ProfileScreen';

const Stack = createNativeStackNavigator<RootStackParamList>();

function App() {
  const { loading, initialRoute, role } = useBootstrap();

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.primary} size="large" />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <StatusBar barStyle="light-content" />
      <NavigationContainer>
        <Stack.Navigator
          initialRouteName={initialRoute}
          screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.background } }}
        >
          <Stack.Screen name="RoleSelect" component={RoleSelectScreen} />
          <Stack.Screen
            name="Login"
            component={LoginScreen}
            initialParams={{ role: role ?? 'CAMERA_DEVICE' }}
          />
          <Stack.Screen
            name="Register"
            component={RegisterScreen}
            initialParams={{ role: role ?? 'CAMERA_DEVICE' }}
          />
          <Stack.Screen name="CameraDevice" component={CameraDeviceScreen} />
          <Stack.Screen name="ViewerDashboard" component={ViewerDashboardScreen} />
          <Stack.Screen name="LiveCamera" component={LiveCameraScreen} options={{ presentation: 'fullScreenModal' }} />
          <Stack.Screen name="EventHistory" component={EventHistoryScreen} />
          <Stack.Screen name="DetectedFaces" component={DetectedFacesScreen} />
          <Stack.Screen name="VideoPlaylist" component={VideoPlaylistScreen} />
          <Stack.Screen
            name="VideoPlayer"
            component={VideoPlayerScreen}
            options={{ presentation: 'fullScreenModal' }}
          />
          <Stack.Screen name="CameraSettings" component={CameraSettingsScreen} />
          <Stack.Screen name="Profile" component={ProfileScreen} />
        </Stack.Navigator>
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, backgroundColor: colors.background, justifyContent: 'center', alignItems: 'center' },
});

export default App;
