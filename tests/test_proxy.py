import pytest

from kangedit import proxy
from kangedit.ffmpeg import FFmpegError
from kangedit.models import Footage

F = Footage("in.mp4", 10.0, 3840, 2160, 30.0, True)


def test_gpu_args_fail_on_decode_errors():
    args = proxy.proxy_args(F, 540, "out.mp4", "d3d11va", "h264_amf")
    assert args[:3] == ["-xerror", "-hwaccel", "d3d11va"]   # garbage frames must not pass silently
    assert "h264_amf" in args and "scale=960:540" in args[args.index("-vf") + 1]
    assert args[args.index("-g") + 1] == str(proxy.GOP) and args[args.index("-bf") + 1] == "0"


def test_cpu_args():
    args = proxy.proxy_args(F, 540, "out.mp4")
    assert "-hwaccel" not in args and "libx264" in args and "format=yuv420p" in args[args.index("-vf") + 1]


@pytest.fixture
def attempts(monkeypatch, tmp_path):
    out = tmp_path / "p.mp4"
    monkeypatch.setattr(proxy, "proxy_path_for", lambda f, h: str(out))
    monkeypatch.setattr(proxy, "hw_decoder", lambda: "d3d11va")
    monkeypatch.setattr(proxy, "hw_encoder", lambda codec: "h264_amf")
    seen = []

    def run(args, duration, progress, cancel, label=""):
        dec = args[args.index("-hwaccel") + 1] if "-hwaccel" in args else None
        enc = args[args.index("-c:v") + 1]
        seen.append((dec, enc))
        if len(seen) <= fail_first[0]:
            raise FFmpegError("hardware accelerator failed to decode picture")
        open(args[-1], "wb").write(b"x")

    fail_first = [0]
    monkeypatch.setattr(proxy, "run_ffmpeg", run)
    return seen, fail_first


def test_gpu_decode_failure_falls_back_to_cpu_decode_then_cpu(attempts):
    seen, fail_first = attempts
    fail_first[0] = 2
    proxy.generate_proxy(F, 540, use_gpu=True)
    assert seen == [("d3d11va", "h264_amf"), (None, "h264_amf"), (None, "libx264")]


def test_cpu_only_when_gpu_off(attempts):
    seen, _ = attempts
    proxy.generate_proxy(F, 540, use_gpu=False)
    assert seen == [(None, "libx264")]
