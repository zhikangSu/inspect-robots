"""V4L2 camera reader backed by OpenCV, opened lazily on the first frame.

``cv2`` is imported on first use so the package imports without OpenCV. The
reader keeps the driver queue short (buffer size 1, one grab discarded per
read) so a frame is at most a couple of frame intervals old at 20 Hz.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
import numpy.typing as npt

from inspect_robots.errors import EmbodimentFault

ImageMap = Mapping[str, npt.NDArray[np.uint8]]
CameraReader = Callable[[], ImageMap]


class OpenCVCameraReader:
    """Read RGB frames from named V4L2 devices at a fixed output resolution."""

    def __init__(self, devices: Mapping[str, str], *, width: int, height: int):
        self._devices = dict(devices)
        self._width = width
        self._height = height
        self._caps: dict[str, Any] = {}
        self._cv2: Any = None

    def _open_all(self) -> None:  # pragma: no cover - real OpenCV
        try:
            import cv2
        except ImportError as exc:
            raise EmbodimentFault(
                "cameras are configured but OpenCV is missing\n"
                'fix: uv pip install "inspect-robots-arx-r5[cameras]"'
            ) from exc
        self._cv2 = cv2
        for name, device in self._devices.items():
            cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
            if not cap.isOpened():
                self.close()
                raise EmbodimentFault(f"camera {name!r} ({device}) could not be opened")
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self._caps[name] = cap

    def __call__(self) -> ImageMap:  # pragma: no cover - real OpenCV
        """Return the newest frame of every camera as ``(H, W, 3)`` RGB uint8."""
        if not self._caps and self._devices:
            self._open_all()
        cv2 = self._cv2
        out: dict[str, npt.NDArray[np.uint8]] = {}
        for name, cap in self._caps.items():
            cap.grab()  # discard one queued frame so read() returns a fresher one
            ok, frame = cap.read()
            if not ok or frame is None:
                raise EmbodimentFault(f"camera {name!r} ({self._devices[name]}) returned no frame")
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            resized = cv2.resize(rgb, (self._width, self._height))
            out[name] = np.asarray(resized, dtype=np.uint8)
        return out

    def close(self) -> None:
        """Release every opened device."""
        for cap in self._caps.values():
            with contextlib.suppress(Exception):  # driver teardown is best effort
                cap.release()
        self._caps.clear()
