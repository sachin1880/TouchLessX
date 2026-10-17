package com.smarthomecamera.camera

import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.view.PixelCopy
import android.view.SurfaceView
import android.view.TextureView
import android.view.View
import android.view.ViewGroup
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.uimanager.UIManagerHelper
import java.io.File
import java.io.FileOutputStream

/**
 * Grabs a still frame from whatever video view is currently on screen (the local preview or
 * a received remote feed) using Android's PixelCopy API. This works regardless of whether
 * react-native-webrtc's RTCView renders via SurfaceView or TextureView under the hood, and -
 * deliberately - never reaches into WebRTC's internal frame pipeline: it just screenshots
 * whatever is currently rendered, the same way Android's own screenshot tooling does. That
 * makes it a one-shot, on-demand capture rather than continuous frame processing.
 */
class MediaCaptureModule(reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName(): String = "MediaCaptureModule"

    @ReactMethod
    fun captureView(viewTag: Int, promise: Promise) {
        val activity = reactApplicationContext.currentActivity
        if (activity == null) {
            promise.reject("NO_ACTIVITY", "App must be in the foreground to capture")
            return
        }

        activity.runOnUiThread {
            try {
                val uiManager = UIManagerHelper.getUIManagerForReactTag(reactApplicationContext, viewTag)
                val view = uiManager?.resolveView(viewTag)
                if (view == null) {
                    promise.reject("VIEW_NOT_FOUND", "Could not find the requested video view")
                    return@runOnUiThread
                }
                captureFromView(view, promise)
            } catch (e: Exception) {
                promise.reject("CAPTURE_FAILED", e.message, e)
            }
        }
    }

    private fun captureFromView(view: View, promise: Promise) {
        val target = findRenderingSurface(view)
        if (target == null) {
            promise.reject("NO_RENDER_SURFACE", "No active video surface found in this view")
            return
        }
        if (target.width <= 0 || target.height <= 0) {
            promise.reject("VIEW_NOT_READY", "Video view has no size yet")
            return
        }

        val bitmap = Bitmap.createBitmap(target.width, target.height, Bitmap.Config.ARGB_8888)

        when (target) {
            is SurfaceView -> {
                val handler = Handler(Looper.getMainLooper())
                PixelCopy.request(
                    target,
                    bitmap,
                    { result ->
                        if (result == PixelCopy.SUCCESS) {
                            promise.resolve(saveBitmap(bitmap))
                        } else {
                            promise.reject("PIXEL_COPY_FAILED", "PixelCopy result code: $result")
                        }
                    },
                    handler,
                )
            }
            is TextureView -> {
                val captured = target.getBitmap(bitmap)
                if (captured != null) {
                    promise.resolve(saveBitmap(bitmap))
                } else {
                    promise.reject("TEXTURE_CAPTURE_FAILED", "TextureView returned no bitmap")
                }
            }
        }
    }

    private fun findRenderingSurface(view: View): View? {
        if (view is SurfaceView || view is TextureView) return view
        if (view is ViewGroup) {
            for (i in 0 until view.childCount) {
                val found = findRenderingSurface(view.getChildAt(i))
                if (found != null) return found
            }
        }
        return null
    }

    private fun saveBitmap(bitmap: Bitmap): String {
        // Both manual capture and the periodic person-detection check land here, the latter
        // as often as every few seconds while streaming - without cleanup these would
        // accumulate indefinitely in cache storage on a phone meant to run 24/7. Each new
        // capture is uploaded (or discarded) immediately, so nothing but the newest file is
        // ever needed locally.
        reactApplicationContext.cacheDir
            .listFiles { f -> f.name.startsWith("capture_") }
            ?.forEach { it.delete() }

        val file = File(reactApplicationContext.cacheDir, "capture_${System.currentTimeMillis()}.jpg")
        FileOutputStream(file).use { out -> bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out) }
        return file.absolutePath
    }
}
