import os
import site
import uuid
from pathlib import Path


def _inject_cuda_path():
    """Inject NVIDIA CUDA DLL dirs into PATH before CTranslate2 loads."""
    for sp in site.getsitepackages():
        nv_dir = os.path.join(sp, "nvidia")
        if not os.path.isdir(nv_dir):
            continue
        for pkg in os.listdir(nv_dir):
            bin_dir = os.path.join(nv_dir, pkg, "bin")
            if os.path.isdir(bin_dir) and bin_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = bin_dir + ";" + os.environ.get("PATH", "")
    # Also check system CUDA Toolkit
    for ver in ("v13.3", "v13.0", "v12.8", "v12.6"):
        cuda_bin = rf"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\{ver}\bin"
        if os.path.isdir(cuda_bin) and cuda_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = cuda_bin + ";" + os.environ.get("PATH", "")


_inject_cuda_path()

import yt_dlp

from src.config import settings
from src.models import SubtitleEntry

_PROJECT_ROOT = Path(__file__).parent.parent
_TEMP_VIDEO_DIR = _PROJECT_ROOT / "tempvideo"

_whisper_model = None
_device = None
_compute_type = None
_model_size = None


def _detect_device():
    global _device, _compute_type, _model_size
    if _device is not None:
        return _device, _compute_type, _model_size

    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            _device = "cuda"
            _compute_type = "float16"
            _model_size = "small"
            print(f"[Whisper] GPU detected → model={_model_size}, device={_device}, compute={_compute_type}")
            return _device, _compute_type, _model_size
    except ImportError:
        pass

    import multiprocessing
    _device = "cpu"
    _compute_type = "int8_float16"
    _model_size = "small"
    n_cores = multiprocessing.cpu_count()
    print(f"[Whisper] No GPU → cpu/{_compute_type}, model={_model_size}, cores={n_cores}")
    return _device, _compute_type, _model_size


def _get_model():
    global _whisper_model
    if _whisper_model is None:
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
        from faster_whisper import WhisperModel
        device, compute, model_size = _detect_device()

        try:
            _whisper_model = WhisperModel(model_size, device=device, compute_type=compute, num_workers=2)
            if device == "cuda":
                import numpy as np
                _whisper_model.encode(np.zeros((1, 80, 3000), dtype=np.float32))
            print(f"[Whisper] Model loaded: {model_size} on {device}/{compute}")
        except Exception as e:
            print(f"[Whisper] GPU init failed ({e}), falling back to CPU")
            device = "cpu"
            model_size = "tiny"
            for compute in ("int8_float16", "int8"):
                try:
                    _whisper_model = WhisperModel(model_size, device=device, compute_type=compute, num_workers=2)
                    print(f"[Whisper] Model loaded: {model_size} on {device}/{compute}")
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError("无法加载 Whisper 模型（CPU/GPU 均不可用）")

    return _whisper_model


def download_audio(url: str, progress_cb=None) -> Path:
    _TEMP_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    tmpdir = _TEMP_VIDEO_DIR / str(uuid.uuid4())[:8]
    tmpdir.mkdir(exist_ok=True)
    out = tmpdir / "audio"

    def hook(d):
        if d["status"] == "downloading" and progress_cb:
            pct = d.get("_percent_str", "0%").strip().rstrip("%")
            try:
                progress_cb(float(pct))
            except ValueError:
                pass

    # Bilibili cookie support for yt-dlp audio download
    bili_opts = {}
    if settings.bilibili_cookies:
        cookie_path = Path(settings.bilibili_cookies)
        if cookie_path.exists():
            bili_opts["cookiefile"] = str(cookie_path.resolve())
        bili_opts.setdefault("extractor_args", {"bilibili": {"skip_login": ["true"]}})

    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "outtmpl": str(out),
        "progress_hooks": [hook],
        **bili_opts,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    candidates = sorted(tmpdir.glob("audio*"), key=lambda p: p.stat().st_size, reverse=True)
    for c in candidates:
        if c.suffix in (".m4a", ".webm", ".opus", ".mp4", ".mkv", ".wav", ".mp3"):
            return c
    if candidates:
        return candidates[0]
    raise RuntimeError("音频下载失败")


def transcribe(audio_path: Path, progress_cb=None) -> list[SubtitleEntry]:
    model = _get_model()
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=1,
        language="zh",
        vad_filter=True,
        word_timestamps=False,
    )

    entries: list[SubtitleEntry] = []
    if progress_cb:
        progress_cb(0)

    for seg in segments:
        entries.append(SubtitleEntry(
            start=round(seg.start, 2),
            end=round(seg.end, 2),
            text=seg.text.strip(),
        ))
        if progress_cb and info.duration > 0:
            progress_cb(min(seg.end / info.duration * 100, 99))

    if progress_cb:
        progress_cb(100)
    return entries


def cleanup_audio(audio_path: Path):
    tmpdir = audio_path.parent
    for f in tmpdir.iterdir():
        try:
            f.unlink()
        except OSError:
            pass
    try:
        tmpdir.rmdir()
    except OSError:
        pass
