from __future__ import annotations

from pathlib import Path

from ..errors import ConfigurationError
from ..utils import sha256_bytes
from .frames import CapturedFrame, FrameCaptureResult

SEEK_SCRIPT = """
async (seconds) => {
  const clips = Array.from(document.querySelectorAll("video.clip"));
  if (!clips.length) return 0;
  await Promise.all(clips.map((video) => new Promise((resolve) => {
    const finish = () => { try { video.pause(); } catch (e) {} resolve(); };
    const target = Math.min(seconds, video.duration || seconds);
    if (video.readyState >= 2 && Math.abs((video.currentTime || 0) - target) < 0.05) {
      return finish();
    }
    const timer = setTimeout(finish, 4000);
    video.addEventListener("seeked", () => { clearTimeout(timer); finish(); }, { once: true });
    try { video.currentTime = target; } catch (e) { clearTimeout(timer); finish(); }
  })));
  return clips.length;
}
"""


class PlaywrightFrameCapture:
    """Deterministic frame capture that can wait for a video seek.

    Chrome's one-shot `--screenshot` cannot wait for `seeked`, so any composition
    containing a clip is rendered through Playwright instead.
    """

    profile_key = "playwright-capture"

    def __init__(self, timeout_ms: int = 30000):
        self.timeout_ms = timeout_ms

    def capture(
        self,
        *,
        html_path: Path,
        frame_count: int,
        fps: int,
        width: int,
        height: int,
    ) -> FrameCaptureResult:
        if frame_count < 1:
            raise ValueError("Frame count must be positive")
        if fps < 1:
            raise ValueError("Frame rate must be positive")
        html_path = Path(html_path).resolve()
        if not html_path.is_file():
            raise FileNotFoundError(f"Composition not found: {html_path}")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise ConfigurationError(
                "playwright is not installed; install the render extra"
            ) from error
        frames: list[CapturedFrame] = []
        version = "playwright/chromium"
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            version = f"playwright/{browser.version}"
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_default_timeout(self.timeout_ms)
            for index in range(frame_count):
                seconds = index / fps
                page.goto(f"{html_path.as_uri()}?t={seconds}", wait_until="load")
                page.evaluate(SEEK_SCRIPT, seconds)
                content = page.screenshot()
                frames.append(
                    CapturedFrame(
                        index=index,
                        time_seconds=f"{seconds:g}",
                        content=content,
                        content_hash=sha256_bytes(content),
                    )
                )
            browser.close()
        return FrameCaptureResult(
            frames=tuple(frames),
            renderer_version=version,
            command_hash=sha256_bytes(f"{html_path.name}:{frame_count}:{fps}".encode()),
        )
