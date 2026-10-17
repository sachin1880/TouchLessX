package com.smarthomecamera.camera

import android.content.Intent
import android.os.SystemClock
import android.provider.Settings
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod

/**
 * Camera capture itself is owned by react-native-webrtc (getUserMedia + RTCView) - Android's
 * Camera2 API only allows one consumer of a camera device at a time, so this module no longer
 * binds the camera directly (an earlier version did, via CameraX, for a local-only preview
 * before streaming existed). What's left here is everything react-native-webrtc doesn't cover:
 * the foreground-service notification that keeps the process alive while streaming, and reading
 * device uptime (SystemClock, not exposed to JS otherwise).
 */
class CameraDeviceModule(reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName(): String = "CameraDeviceModule"

    @ReactMethod
    fun startForegroundNotification(promise: Promise) {
        try {
            ContextCompat.startForegroundService(
                reactApplicationContext,
                Intent(reactApplicationContext, CameraKeepAliveService::class.java),
            )
            promise.resolve(true)
        } catch (e: Exception) {
            promise.reject("FOREGROUND_SERVICE_START_FAILED", e.message, e)
        }
    }

    @ReactMethod
    fun stopForegroundNotification(promise: Promise) {
        try {
            reactApplicationContext.stopService(
                Intent(reactApplicationContext, CameraKeepAliveService::class.java),
            )
            promise.resolve(true)
        } catch (e: Exception) {
            promise.reject("FOREGROUND_SERVICE_STOP_FAILED", e.message, e)
        }
    }

    @ReactMethod
    fun getUptimeMillis(promise: Promise) {
        promise.resolve(SystemClock.elapsedRealtime().toDouble())
    }

    /**
     * Settings.Secure.ANDROID_ID, unlike react-native-device-info's getUniqueId() (which
     * generates and caches a random value in the app's own storage), is tied to the device +
     * signing key and survives an uninstall/reinstall - required so this camera doesn't
     * re-register as a "new" device every time the app is reinstalled during development or
     * updates.
     */
    @ReactMethod
    fun getStableDeviceId(promise: Promise) {
        try {
            val id = Settings.Secure.getString(reactApplicationContext.contentResolver, Settings.Secure.ANDROID_ID)
            promise.resolve(id)
        } catch (e: Exception) {
            promise.reject("ANDROID_ID_UNAVAILABLE", e.message, e)
        }
    }
}
