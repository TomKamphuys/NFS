# GUI workflow tests

`tests/test_qt_jog_workflow.py` opens the real `MainWindow` with the real
backend, scanner, workers, and Qt timers. `pytest-qt` delivers mouse events to
the actual jog and Home buttons; assertions check simulated positions, not
just handler calls. GRBL uses `MockSimulatedDRO` and audio uses
`MockInterfaceAudio`. Configuration and project output are isolated in pytest's
temporary directory; no scanner hardware is required.

Coverage includes disabled controls before loading, automatic enablement after
loading, both directions on all three axes, repeated jogging, Home followed by
jogging, and recovery after a simulated controller error. Startup loading is
started with the same backend worker pattern as the application entry point;
there is no Load Scanner button. These tests do not invoke `main()` or test its
splash screen. Completion is awaited through real Qt signals with deadlines,
without manually refreshing the pane or calling completion handlers.

From PowerShell:

```powershell
uv sync --locked --all-extras --dev
uv run pytest tests\test_qt_jog_workflow.py -v
```

By default the window renders offscreen. To watch the same tests on Windows:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
uv run pytest tests\test_qt_jog_workflow.py -v
Remove-Item Env:QT_QPA_PLATFORM
```

The tests are included in normal `pytest` discovery and can also be selected
with `-m gui_smoke`. Missing Qt or pytest-qt dependencies fail rather than skip
this suite. The Python package workflow runs them within the Linux suite and
in a dedicated Windows job on pushes and pull requests to `master`.

These are Qt-level clicks, not operating-system mouse automation. They use the
application's matplotlib viewer option, not PyVista rendering, and cannot
validate real firmware, physical motion, or audio-device behavior.