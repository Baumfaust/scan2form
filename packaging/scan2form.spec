# PyInstaller spec for the Windows app. Run via `uv run python tools/build_windows.py`,
# which first stages the OCR models in build/ocr-models (they are not in the repo).
# ruff: noqa: F821  (Analysis, PYZ, EXE, COLLECT and SPECPATH are provided by PyInstaller)
from pathlib import Path

ROOT = Path(SPECPATH).parent
SITE_RAPIDOCR = Path(__import__("rapidocr").__file__).parent

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=[
        (str(ROOT / "src" / "scan2form" / "ui" / "assets"), "scan2form/ui/assets"),
        (str(ROOT / "build" / "ocr-models"), "ocr-models"),
        (str(SITE_RAPIDOCR / "config.yaml"), "rapidocr"),
        (str(SITE_RAPIDOCR / "default_models.yaml"), "rapidocr"),
    ],
    hiddenimports=["keyring.backends.Windows"],
    # Local-evaluation extras (AGPL) and big libraries the app never uses.
    excludes=["ultralytics", "torch", "torchvision", "huggingface_hub", "matplotlib",
              "tkinter", "pandas", "IPython", "scan2form.spike", "pytest", "reportlab"],
    noarchive=False,
)


def needed(entry) -> bool:
    """Drop what the app never uses: OpenCV's video I/O (FFmpeg), Qt's software OpenGL
    fallback and Qt translations other than German/English (~55 MB)."""
    name = entry[0].replace("\\", "/").lower()
    if "opencv_videoio_ffmpeg" in name or name.endswith("opengl32sw.dll"):
        return False
    if "/translations/" in name and not any(f"_{lang}.qm" in name for lang in ("de", "en")):
        return False
    return True


a.binaries = [b for b in a.binaries if needed(b)]
a.datas = [d for d in a.datas if needed(d)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Scan2Form",
    icon=str(ROOT / "src" / "scan2form" / "ui" / "assets" / "icon.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Scan2Form", upx=False)
