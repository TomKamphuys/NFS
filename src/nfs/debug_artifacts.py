"""
Debug Artifact Handlers
=======================

Handles saving intermediate debug artifacts (conditioned mic signals,
loop-aligned signals, individual sweep slices, metrics JSON) produced
during IR capture.

The handler is injected into the Audio class, allowing the capture logic
to remain unaware of whether debug artifacts are saved to disk, discarded,
or sent elsewhere.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import soundfile as sf

from loguru import logger


class DebugArtifactHandler(ABC):
    """Handles saving intermediate debug artifacts from IR capture."""

    @abstractmethod
    def save_wav(self, data: np.ndarray, label: str, session_dir: Path) -> None:
        pass

    @abstractmethod
    def save_metrics(self, metrics: dict, label: str, session_dir: Path) -> None:
        pass


class FileDebugArtifactHandler(DebugArtifactHandler):
    """Saves debug artifacts as WAV/JSON files in the debug subdirectory."""

    def __init__(self, hw_config: Dict[str, Any]):
        self.hw = hw_config

    def _debug_dir(self, session_dir: Path) -> Path:
        d = session_dir / "debug"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_wav_with_metadata(self, filepath: Path, data: np.ndarray, title: str,
                                 subtype: Optional[str] = None) -> None:
        channels = data.shape[1] if len(data.shape) > 1 else 1
        kwargs = {'mode': 'w', 'samplerate': self.hw['fs'], 'channels': channels}
        if subtype:
            kwargs['subtype'] = subtype
        with sf.SoundFile(str(filepath), **kwargs) as f:
            f.title = title
            f.comment = title
            f.write(data)

    def save_wav(self, data: np.ndarray, label: str, session_dir: Path) -> None:
        filepath = self._debug_dir(session_dir) / f"{label}.wav"
        self._save_wav_with_metadata(filepath, data, label)

    def save_metrics(self, metrics: dict, label: str, session_dir: Path) -> None:
        try:
            import json
            filepath = self._debug_dir(session_dir) / f"{label}_metrics.json"
            with open(filepath, "w") as f:
                json.dump(metrics, f, indent=4)
        except Exception as e:
            logger.warning(f"Failed to save metrics JSON: {e}")


class NoopDebugArtifactHandler(DebugArtifactHandler):
    """Discards all debug artifacts — used when debug saves are disabled."""

    def save_wav(self, data: np.ndarray, label: str, session_dir: Path) -> None:
        pass

    def save_metrics(self, metrics: dict, label: str, session_dir: Path) -> None:
        pass


class DebugArtifactFactory:
    """Factory for creating DebugArtifactHandler instances from config values."""

    @staticmethod
    def create(debug_saves: bool, hw_config: Optional[Dict[str, Any]] = None) -> DebugArtifactHandler:
        if debug_saves:
            return FileDebugArtifactHandler(hw_config)
        else:
            return NoopDebugArtifactHandler()
