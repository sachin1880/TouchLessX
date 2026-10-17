module.exports = {
  preset: '@react-native/jest-preset',
  setupFiles: ['./jest.setup.js'],
  transformIgnorePatterns: [
    'node_modules/(?!(@react-native|react-native|@react-navigation|react-native-screens|react-native-safe-area-context|@react-native-community|@react-native-async-storage|react-native-device-info|react-native-vector-icons|@react-native-ml-kit)/)',
  ],
};
