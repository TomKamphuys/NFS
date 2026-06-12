from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from harmonic_drive import control


@pytest.mark.anyio
async def test_zero_skips_manual_height_offset_when_zero(monkeypatch):
    scanner = Mock()
    monkeypatch.setattr(control, "scanner_app", SimpleNamespace(scanner=scanner))

    async def immediate(func, *args):
        return func(*args)

    monkeypatch.setattr(control.run, "io_bound", immediate)

    await control.zero_nfs_then_apply_height_offset(0)

    scanner.set_as_zero.assert_called_once_with()
    scanner.set_speaker_center_above_stool.assert_not_called()


@pytest.mark.anyio
async def test_zero_applies_manual_height_offset_when_nonzero(monkeypatch):
    scanner = Mock()
    monkeypatch.setattr(control, "scanner_app", SimpleNamespace(scanner=scanner))

    async def immediate(func, *args):
        return func(*args)

    monkeypatch.setattr(control.run, "io_bound", immediate)

    await control.zero_nfs_then_apply_height_offset(12.5)

    scanner.set_as_zero.assert_called_once_with()
    scanner.set_speaker_center_above_stool.assert_called_once_with(12.5)
