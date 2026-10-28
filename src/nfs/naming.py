"""
IR Naming Conventions
=====================

Strategy classes for building IR and distortion output file paths from
a cylindrical measurement position.

The naming convention is a policy concern — it determines how measurement
files are named on disk — and is kept separate from the audio signal
processing logic in audio.py.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from .datatypes import CylindricalPosition
from .utils.dsp import DSPUtils


class IRNamingConvention(ABC):
    """Strategy for building IR and distortion output paths from a position."""

    @abstractmethod
    def build_ir_path(self, position: CylindricalPosition, session_dir: Path, prefix: str | None = None) -> Path:
        pass

    @abstractmethod
    def build_dist_path(self, position: CylindricalPosition, session_dir: Path, prefix: str | None = None) -> Path:
        pass


class TomNamingConvention(IRNamingConvention):
    """Tom's format: (r, t, z).wav / (r, t, z)_dist.wav"""

    def build_ir_path(self, position: CylindricalPosition, session_dir: Path, prefix: str | None = None) -> Path:
        base = f"({position.r():.1f}, {position.t():.1f}, {position.z():.1f})"
        return session_dir / f"{base}.wav"

    def build_dist_path(self, position: CylindricalPosition, session_dir: Path, prefix: str | None = None) -> Path:
        base = f"({position.r():.1f}, {position.t():.1f}, {position.z():.1f})"
        dist_dir = session_dir / "Distortion"
        return dist_dir / f"{base}_dist.wav"


class DimitriNamingConvention(IRNamingConvention):
    """Dimitri's format: rX_phY_zZ_ir.wav / rX_phY_zZ_ir_dist.wav
    
    If a prefix is provided, it is prepended to the filename (e.g., for the project name).
    """

    def _prefix_str(self, prefix: str | None) -> str:
        return f"{prefix}_" if prefix else ""

    def build_ir_path(self, position: CylindricalPosition, session_dir: Path, prefix: str | None = None) -> Path:
        base = (
            f"r{DSPUtils.fmt_num_for_name(position.r())}_"
            f"ph{DSPUtils.fmt_num_for_name(position.t())}_"
            f"z{DSPUtils.fmt_num_for_name(position.z())}"
        )
        return session_dir / f"{self._prefix_str(prefix)}{base}_ir.wav"

    def build_dist_path(self, position: CylindricalPosition, session_dir: Path, prefix: str | None = None) -> Path:
        base = (
            f"r{DSPUtils.fmt_num_for_name(position.r())}_"
            f"ph{DSPUtils.fmt_num_for_name(position.t())}_"
            f"z{DSPUtils.fmt_num_for_name(position.z())}"
        )
        dist_dir = session_dir / "Distortion"
        return dist_dir / f"{self._prefix_str(prefix)}{base}_ir_dist.wav"


class NamingConventionFactory:
    """Factory for creating IRNamingConvention instances from a config string."""

    @staticmethod
    def create(naming_convention: str) -> IRNamingConvention:
        if naming_convention == 'dimitri':
            return DimitriNamingConvention()
        else:
            return TomNamingConvention()
