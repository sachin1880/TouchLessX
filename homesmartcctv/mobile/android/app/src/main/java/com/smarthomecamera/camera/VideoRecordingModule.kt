package com.smarthomecamera.camera

import android.os.Handler
import android.os.HandlerThread
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.oney.WebRTCModule.WebRTCModule
import org.webrtc.EglBase
import org.webrtc.MediaStreamTrack
import org.webrtc.VideoFileRenderer
import org.webrtc.VideoTrack
import java.io.File

/**
 * Records the camera's own local video track to a sequence of MP4 segments, independent of
 * whatever is or isn't currently rendered on screen. Manual capture (MediaCaptureModule) and
 * the old face-detection approach both grab frames via PixelCopy from the on-screen
 * RTCView - which stops working the moment the screen locks, because Android tears down the
 * view's render surface. Continuous 24/7 recording can't have that dependency, so this
 * attaches a VideoSink directly to the WebRTC video track instead: a sink added this way
 * keeps receiving frames from the camera regardless of UI/screen state, the same way WebRTC
 * delivers frames to the network encoder for the live call itself.
 *
 * react-native-webrtc doesn't expose a public way to resolve its internal native VideoTrack
 * from a track id (WebRTCModule.getLocalTrack() is package-private), so this reaches it via
 * reflection rather than patching the dependency - reflection fails loudly and catchably if a
 * future react-native-webrtc upgrade renames something, instead of silently going stale the
 * way an unmaintained patch file could.
 *
 * The actual frame-to-MP4 encoding is WebRTC's own org.webrtc.VideoFileRenderer, the same
 * utility the WebRTC project itself ships for local call recording - not something written
 * from scratch here.
 */
class VideoRecordingModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName(): String = "VideoRecordingModule"

    private val thread = HandlerThread("VideoRecording").apply { start() }
    private val handler = Handler(thread.looper)

    private var eglBase: EglBase? = null
    private var renderer: VideoFileRenderer? = null
    private var recordingTrack: VideoTrack? = null

    private fun resolveNativeVideoTrack(trackId: String): VideoTrack? {
        val webRTCModule = reactContext.getNativeModule(WebRTCModule::class.java) ?: return null
        return try {
            val method = WebRTCModule::class.java.getDeclaredMethod("getLocalTrack", String::class.java)
            method.isAccessible = true
            (method.invoke(webRTCModule, trackId) as? MediaStreamTrack) as? VideoTrack
        } catch (e: Exception) {
            null
        }
    }

    @ReactMethod
    fun startSegment(trackId: String, fileName: String, width: Int, height: Int, promise: Promise) {
        handler.post {
            try {
                val track = resolveNativeVideoTrack(trackId)
                if (track == null) {
                    promise.reject("TRACK_NOT_FOUND", "Could not resolve the native video track for id $trackId")
                    return@post
                }

                val outputFile = File(reactContext.cacheDir, fileName)
                outputFile.parentFile?.mkdirs()
                val base = EglBase.create()
                val fileRenderer = VideoFileRenderer(outputFile.absolutePath, width, height, base.eglBaseContext)
                track.addSink(fileRenderer)

                eglBase = base
                renderer = fileRenderer
                recordingTrack = track
                promise.resolve(outputFile.absolutePath)
            } catch (e: Exception) {
                promise.reject("START_SEGMENT_FAILED", e.message, e)
            }
        }
    }

    @ReactMethod
    fun stopSegment(promise: Promise) {
        val track = recordingTrack
        val fileRenderer = renderer
        val base = eglBase
        recordingTrack = null
        renderer = null
        eglBase = null

        if (track == null || fileRenderer == null) {
            promise.resolve(false)
            return
        }

        handler.post {
            try {
                track.removeSink(fileRenderer)
                fileRenderer.release()
                base?.release()
                promise.resolve(true)
            } catch (e: Exception) {
                promise.reject("STOP_SEGMENT_FAILED", e.message, e)
            }
        }
    }
}
