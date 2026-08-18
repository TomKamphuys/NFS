"""Small setup wizard for the otherwise headless audio diagnostic."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nfs.audio import get_devices_and_channels
from nfs.audio_diagnostic import DiagnosticRequest, request_from_config, write_request

from .qt_compat import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class AudioDiagnosticSetupDialog(QDialog):
    """Confirmation-first UI; technical selectors remain hidden by default."""

    def __init__(self, config_path: str, parent=None) -> None:
        super().__init__(parent)
        self.config_path = str(Path(config_path).resolve())
        self.catalog = get_devices_and_channels()
        self.request = request_from_config(
            self.config_path,
            Path.cwd() / "audio_diagnostics",
        )
        self.request_path: Path | None = None
        self.setWindowTitle("NFS Audio Diagnostic Setup")
        self.setMinimumWidth(570)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        heading = QLabel("Electrical loopback audio diagnostic")
        heading.setStyleSheet("font-size: 15pt; font-weight: 600;")
        root.addWidget(heading)

        explanation = QLabel(
            "This test uses the interface and loopback channels already saved in Audio Setup. "
            "It will run without a graphical interface after you press Start."
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        self.summary = QLabel()
        self.summary.setTextInteractionFlags(self.summary.textInteractionFlags())
        self.summary.setStyleSheet("background: #f3f4f6; padding: 10px; border-radius: 4px;")
        root.addWidget(self.summary)

        self.change_button = QPushButton("Change setup")
        self.change_button.setCheckable(True)
        self.change_button.toggled.connect(self._toggle_advanced)
        root.addWidget(self.change_button)

        self.advanced = QGroupBox("Connection used for the test")
        advanced_layout = QVBoxLayout(self.advanced)
        self.device_combo = QComboBox()
        self.input_combo = QComboBox()
        self.output_combo = QComboBox()
        advanced_layout.addWidget(QLabel("ASIO interface"))
        advanced_layout.addWidget(self.device_combo)
        advanced_layout.addWidget(QLabel("Physical line output connected to the cable"))
        advanced_layout.addWidget(self.output_combo)
        advanced_layout.addWidget(QLabel("Physical line input receiving the cable"))
        advanced_layout.addWidget(self.input_combo)
        root.addWidget(self.advanced)
        self.advanced.hide()

        warning = QLabel(
            "Before continuing:\n"
            "• Connect a physical LINE output to a physical LINE input with a suitable cable.\n"
            "• Do not use TotalMix, Focusrite or ASIO4ALL software loopback.\n"
            "• Turn phantom power off and mute or disconnect loudspeakers.\n"
            "• Never connect a power-amplifier or speaker-level output to the input."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #7c2d12; background: #fff7ed; padding: 10px;")
        root.addWidget(warning)

        self.confirm = QCheckBox(
            "I have connected the physical line output shown above to the physical line input, "
            "with phantom power off."
        )
        root.addWidget(self.confirm)

        duration = QLabel(
            "The setup window will disappear during testing. Do not use the computer until the "
            "completion window returns. Coarse progress will be printed in the terminal."
        )
        duration.setWordWrap(True)
        root.addWidget(duration)

        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        self.start = QPushButton("Start diagnostic")
        self.start.setEnabled(False)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        buttons.addWidget(self.start)
        root.addLayout(buttons)

        cancel.clicked.connect(self.reject)
        self.start.clicked.connect(self._accept_setup)
        self.confirm.toggled.connect(self.start.setEnabled)
        self.device_combo.currentIndexChanged.connect(self._refresh_channels)
        self.input_combo.currentIndexChanged.connect(self._refresh_summary_from_controls)
        self.output_combo.currentIndexChanged.connect(self._refresh_summary_from_controls)
        self._populate_devices()
        self._refresh_summary()

    def _full_duplex_devices(self):
        devices = [
            (int(dev_id), info)
            for dev_id, info in self.catalog.items()
            if info.get("input_channels") and info.get("output_channels")
        ]
        asio = [item for item in devices if "ASIO" in str(item[1].get("hostapi", "")).upper()]
        yield from (asio or devices)

    def _populate_devices(self) -> None:
        target = (
            self.request.input_device_name.casefold(),
            self.request.output_device_name.casefold(),
            self.request.input_hostapi.casefold(),
            self.request.output_hostapi.casefold(),
        )
        selected = -1
        self.device_combo.blockSignals(True)
        for dev_id, info in self._full_duplex_devices():
            label = f"{info['name']} — {info['hostapi']}"
            self.device_combo.addItem(label, dev_id)
            if (
                str(info["name"]).casefold() == target[0]
                and str(info["name"]).casefold() == target[1]
                and str(info["hostapi"]).casefold() == target[2]
                and str(info["hostapi"]).casefold() == target[3]
            ):
                selected = self.device_combo.count() - 1
        if selected >= 0:
            self.device_combo.setCurrentIndex(selected)
        self.device_combo.blockSignals(False)
        self._refresh_channels()

    def _refresh_channels(self) -> None:
        dev_id = self.device_combo.currentData()
        if dev_id is None:
            return
        info = self.catalog[int(dev_id)]
        self.input_combo.blockSignals(True)
        self.output_combo.blockSignals(True)
        self.input_combo.clear()
        self.output_combo.clear()
        for channel in info.get("input_channels", []):
            self.input_combo.addItem(f"Input channel {int(channel) + 1}", int(channel))
        for channel in info.get("output_channels", []):
            self.output_combo.addItem(f"Output channel {int(channel) + 1}", int(channel))
        input_index = self.input_combo.findData(self.request.input_channel)
        output_index = self.output_combo.findData(self.request.output_channel)
        self.input_combo.setCurrentIndex(max(0, input_index))
        self.output_combo.setCurrentIndex(max(0, output_index))
        self.input_combo.blockSignals(False)
        self.output_combo.blockSignals(False)
        self._refresh_summary_from_controls()

    def _selected_request(self) -> DiagnosticRequest:
        dev_id = self.device_combo.currentData()
        if dev_id is None:
            return self.request
        info = self.catalog[int(dev_id)]
        selected = DiagnosticRequest(**self.request.__dict__)
        selected.input_device_name = str(info["name"])
        selected.output_device_name = str(info["name"])
        selected.input_hostapi = str(info["hostapi"])
        selected.output_hostapi = str(info["hostapi"])
        selected.input_channel = int(self.input_combo.currentData() or 0)
        selected.output_channel = int(self.output_combo.currentData() or 0)
        return selected

    def _refresh_summary_from_controls(self) -> None:
        if self.device_combo.count():
            self.request = self._selected_request()
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        self.summary.setText(
            f"<b>Interface:</b> {self.request.input_device_name} — {self.request.input_hostapi}<br>"
            f"<b>Connect:</b> physical output channel {self.request.output_channel + 1} "
            f"→ physical input channel {self.request.input_channel + 1}<br>"
            f"<b>Current app setting:</b> {self.request.sample_rate} Hz, "
            f"{'Automatic' if self.request.blocksize == 0 else self.request.blocksize} samples"
        )

    def _toggle_advanced(self, visible: bool) -> None:
        self.advanced.setVisible(visible)
        self.change_button.setText("Hide setup choices" if visible else "Change setup")

    def _accept_setup(self) -> None:
        if not self.confirm.isChecked():
            return
        if not self.request.input_device_name or not self.request.output_device_name:
            QMessageBox.warning(self, "Audio device required", "Select a full-duplex audio interface first.")
            return
        request_dir = Path(self.request.output_root) / ".requests"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.request_path = write_request(self.request, request_dir / f"request_{stamp}.json")
        self.accept()
