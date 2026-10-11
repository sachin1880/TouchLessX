# PyInstaller build description for the TouchlessX desktop application.
# Build with:  scripts/build_macos.sh   (macOS)   or   pyinstaller TouchlessX.spec   (Windows)
import sys

from PyInstaller.utils.hooks import collect_all

VERSION = "1.3.0"
datas, binaries, hiddenimports = [], [], []
# Packages that carry model graphs, themes or native libraries PyInstaller can't see by itself.
for package in ("mediapipe", "customtkinter", "vosk", "faster_whisper", "ctranslate2", "sounddevice",
                "_sounddevice_data", "tokenizers", "onnxruntime"):
    try:
        found = collect_all(package)
    except Exception:
        continue
    datas += found[0]
    binaries += found[1]
    hiddenimports += found[2]
hiddenimports += ["sklearn.neighbors", "sklearn.metrics", "sklearn.model_selection", "sklearn.utils._typedefs",
                  "sklearn.neighbors._partition_nodes", "PIL._tkinter_finder"]
if sys.platform == "darwin":
    hiddenimports += ["ApplicationServices", "Quartz", "AppKit", "Foundation", "objc"]

analysis = Analysis(
    ["app.py"], pathex=["."], datas=datas, binaries=binaries, hiddenimports=hiddenimports,
    excludes=["pytest", "jax", "jaxlib", "tensorflow", "torch", "IPython", "notebook"],
)
archive = PYZ(analysis.pure)
executable = EXE(archive, analysis.scripts, [], exclude_binaries=True, name="TouchlessX", console=False)
bundle = COLLECT(executable, analysis.binaries, analysis.datas, name="TouchlessX")
if sys.platform == "darwin":
    app = BUNDLE(
        bundle, name="TouchlessX.app", bundle_identifier="com.touchlessx.app", version=VERSION,
        info_plist={
            # macOS closes an app that uses the camera or microphone without saying why.
            "NSCameraUsageDescription": "TouchlessX watches your hand through the camera to recognise gestures. "
                                        "Frames are processed on this Mac and never saved or sent anywhere.",
            "NSMicrophoneUsageDescription": "TouchlessX listens for the wake word and your spoken commands. "
                                            "Audio is processed on this Mac and never saved or sent anywhere.",
            "NSHighResolutionCapable": True,
            # The bundled libraries (MediaPipe, OpenCV, the speech engines) are built for macOS 14+.
            "LSMinimumSystemVersion": "14.0",
            "CFBundleShortVersionString": VERSION,
        },
    )
