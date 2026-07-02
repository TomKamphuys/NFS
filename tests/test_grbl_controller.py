import pytest
from unittest.mock import Mock, patch
from nfs.grbl_controller import (
    EventHandler,
    GrblControllerMock,
    GrblControllerMockSimulatedDRO,
    ESP32Duino,
    GrblMachineState,
    CylindricalPosition,
    GrblControllerFactory,
    GrblStreamerClientConnection
)
import time


def test_event_handler_initialization():
    handler = EventHandler()
    assert handler.get_received_message() == ''
    assert handler.get_current_position() is None
    assert handler.get_machine_position() is None
    assert handler.get_state() == GrblMachineState.IDLE
    assert handler.get_state_raw() == "Idle"


def test_event_handler_on_rx_buffer_percent():
    handler = EventHandler()
    handler.on_grbl_event("on_rx_buffer_percent", 50)
    assert handler.get_received_message() == 'ok'


def test_event_handler_on_stateupdate():
    handler = EventHandler()
    # data format for on_stateupdate: (mode, mpos, wpos)
    # positions are tuples (X, Y, Z)
    # CylindricalPosition(wpos[1], wpos[2], wpos[0]) -> (Y, Z, X)
    mpos = (1.0, 2.0, 3.0)
    wpos = (10.0, 20.0, 30.0)  # X=10, Y=20, Z=30
    handler.on_grbl_event("on_stateupdate", "Run", mpos, wpos)

    assert handler.get_state() == GrblMachineState.RUN
    assert handler.get_state_raw() == "Run"
    pos = handler.get_current_position()
    assert pos.r() == 20.0  # Y
    assert pos.t() == 30.0  # Z
    assert pos.z() == 10.0  # X
    machine_pos = handler.get_machine_position()
    assert machine_pos.r() == 2.0
    assert machine_pos.t() == 3.0
    assert machine_pos.z() == 1.0


def test_event_handler_on_stateupdate_callback_error():
    handler = EventHandler()
    callback = Mock(side_effect=Exception("Callback failed"))
    handler.set_on_state_update_callback(callback)

    wpos = (10.0, 20.0, 30.0)
    # Should not raise exception because it's caught in EventHandler
    handler.on_grbl_event("on_stateupdate", "Idle", (1.0, 2.0, 3.0), wpos)
    callback.assert_called_once()


def test_event_handler_on_stateupdate_supports_legacy_callback():
    handler = EventHandler()
    calls = []
    def callback(pos, state):
        calls.append((pos, state))

    handler.set_on_state_update_callback(callback)

    handler.on_grbl_event("on_stateupdate", "Idle", (1.0, 2.0, 3.0), (4.0, 5.0, 6.0))

    pos = handler.get_current_position()
    assert calls == [(pos, GrblMachineState.IDLE)]


def test_grbl_streamer_client_connection():
    mock_streamer = Mock()
    handler = EventHandler()
    conn = GrblStreamerClientConnection(mock_streamer, handler)

    conn.killalarm()
    mock_streamer.killalarm.assert_called_once()

    conn.softreset()
    mock_streamer.softreset.assert_called_once()

    conn.hold()
    mock_streamer.send_immediately.assert_called_with('!')

    conn.send("G0 X10")
    mock_streamer.send_immediately.assert_called_with("G0 X10")

    handler.set_received_message("some message")
    assert conn.receive() == "some message"
    assert handler.get_received_message() == ""

    handler.on_grbl_event("on_stateupdate", "Idle", (4, 5, 6), (1, 2, 3))
    assert conn.get_position() == CylindricalPosition(2, 3, 1)
    assert conn.get_machine_position() == CylindricalPosition(5, 6, 4)
    assert conn.get_state() == GrblMachineState.IDLE
    assert conn.get_state_raw() == "Idle"

    callback = Mock()
    conn.set_on_state_update_callback(callback)
    assert handler._on_state_update_callback == callback

    conn.close()
    mock_streamer.disconnect.assert_called_once()


def test_grbl_streamer_client_connection_force_closes_open_iface_when_not_connected():
    iface = Mock()
    mock_streamer = Mock()
    mock_streamer.__dict__["_iface"] = iface
    mock_streamer.__dict__["_thread_polling"] = None
    mock_streamer.__dict__["_thread_read_iface"] = None
    mock_streamer.__dict__["_queue"] = Mock()
    mock_streamer.connected = False
    handler = EventHandler()
    conn = GrblStreamerClientConnection(mock_streamer, handler)

    conn.close()

    mock_streamer.disconnect.assert_called_once()
    iface.stop.assert_called_once()
    assert mock_streamer._iface is None
    assert mock_streamer.connected is False


def test_grbl_streamer_client_connection_force_closes_reader_thread():
    read_thread = Mock()
    read_thread.is_alive.side_effect = [True, False]
    queue_obj = Mock()
    mock_streamer = Mock()
    mock_streamer.__dict__["_iface"] = Mock()
    mock_streamer.__dict__["_thread_polling"] = None
    mock_streamer.__dict__["_thread_read_iface"] = read_thread
    mock_streamer.__dict__["_queue"] = queue_obj
    handler = EventHandler()
    conn = GrblStreamerClientConnection(mock_streamer, handler)

    conn.close()

    queue_obj.put.assert_called_once_with("dummy_msg_for_joining_thread")
    read_thread.join.assert_called_once_with(timeout=2.0)
    assert mock_streamer._thread_read_iface is None


def test_event_handler_on_error():
    handler = EventHandler()
    with pytest.raises(Exception, match="ERROR: event=on_error"):
        handler.on_grbl_event("on_error", "Some error message")


def test_event_handler_on_alarm():
    handler = EventHandler()
    handler.on_grbl_event("on_alarm")
    assert handler.get_received_message() == 'ok'
    assert handler.get_state() == GrblMachineState.ALARM
    assert handler.get_state_raw() == "Alarm"


def test_grbl_controller_mock():
    mock = GrblControllerMock()
    assert mock.get_state() == GrblMachineState.IDLE
    assert mock.get_position() == CylindricalPosition(0, 0, 0)

    mock.send("G0 X10")
    mock.send_and_wait_for_move_ready("G0 Y20")
    mock.killalarm()
    mock.softreset()
    mock.hold()
    mock.shutdown()
    mock.force_position_update()

    assert mock.get_state_raw() == "Idle"
    mock.set_on_state_update_callback(lambda p, s: None)


def test_grbl_controller_mock_simulated_dro_emits_motion_updates():
    from nfs.grbl_controller import GrblControllerMockSimulatedDRO

    mock = GrblControllerMockSimulatedDRO(
        linear_speed_mm_s=50.0,
        angular_speed_deg_s=50.0,
        status_hz=100.0,
    )
    updates = []
    mock.set_on_state_update_callback(
        lambda position, state, machine_position=None: updates.append(
            (position, state, machine_position)
        )
    )

    mock.send_and_wait_for_move_ready("G0 X10 Y20 Z30")

    assert mock.get_state() == GrblMachineState.IDLE
    assert mock.get_position() == CylindricalPosition(20, 30, 10)
    assert any(state == GrblMachineState.RUN for _position, state, _machine_position in updates)
    assert updates[-1][1] == GrblMachineState.IDLE
    assert updates[-1][0] == CylindricalPosition(20, 30, 10)
    assert len(updates) > 2


def test_esp32_duino_initialization():
    mock_conn = Mock()
    # __init__ calls _unlock, which must bypass the guarded send path while in Alarm.
    mock_conn.receive.return_value = "ok"

    controller = ESP32Duino(mock_conn)
    mock_conn.killalarm.assert_called_once()
    mock_conn.send.assert_not_called()


def test_esp32_duino_initialization_times_out_without_grbl_response(monkeypatch):
    mock_conn = Mock()
    mock_conn.receive.return_value = ""
    times = iter([0.0, 4.0])
    monkeypatch.setattr("nfs.grbl_controller.time.monotonic", lambda: next(times))
    monkeypatch.setattr("nfs.grbl_controller.time.sleep", lambda _s: None)

    with pytest.raises(TimeoutError, match="No GRBL response"):
        ESP32Duino(mock_conn)
    mock_conn.killalarm.assert_called_once()


def test_esp32_duino_can_skip_controller_verification():
    mock_conn = Mock()

    ESP32Duino(mock_conn, verify_on_connect=False)

    mock_conn.send.assert_not_called()
    mock_conn.receive.assert_not_called()


def test_esp32_duino_send():
    mock_conn = Mock()
    # 1. Init: _unlock calls send with a probe timeout. Need "ok".
    # 2. Test send("G0 X10"): waits for ack without a movement timeout. Need "ok".
    mock_conn.receive.side_effect = ["ok", "", "ok"]

    controller = ESP32Duino(mock_conn)
    mock_conn.send.reset_mock()

    controller.send("G0 X10")
    mock_conn.send.assert_called_with("G0 X10\n")
    assert mock_conn.receive.call_count >= 3


def test_esp32_duino_unlock_bypasses_guarded_send_path():
    mock_conn = Mock()
    mock_conn.receive.return_value = "ok"

    ESP32Duino(mock_conn)

    mock_conn.killalarm.assert_called_once()
    assert not any(
        call.args == ("$X\n",)
        for call in mock_conn.send.call_args_list
    )


def test_esp32_duino_send_does_not_use_probe_timeout(monkeypatch):
    mock_conn = Mock()
    mock_conn.receive.return_value = "ok"
    controller = ESP32Duino.__new__(ESP32Duino)
    controller._connection = mock_conn

    def fail_monotonic():
        raise AssertionError("normal sends should not use the probe timeout")

    monkeypatch.setattr("nfs.grbl_controller.time.monotonic", fail_monotonic)

    controller.send("G0 X10")
    mock_conn.send.assert_called_with("G0 X10\n")


def test_esp32_duino_send_and_wait_for_move_ready():
    mock_conn = Mock()
    mock_conn.receive.return_value = "ok"
    # _wait_for_idle_state will call get_state()
    mock_conn.get_state.side_effect = [GrblMachineState.RUN, GrblMachineState.IDLE]

    controller = ESP32Duino(mock_conn)
    controller.send_and_wait_for_move_ready("G0 X10")

    mock_conn.send.assert_any_call("G0 X10\n")
    mock_conn.send.assert_any_call("G04 P0\n")
    assert mock_conn.get_state.call_count == 2


def test_esp32_duino_getters_and_setters():
    mock_conn = Mock()
    mock_conn.receive.return_value = "ok"
    controller = ESP32Duino(mock_conn)

    mock_conn.get_position.return_value = CylindricalPosition(1, 2, 3)
    assert controller.get_position() == CylindricalPosition(1, 2, 3)

    mock_conn.get_state.return_value = GrblMachineState.ALARM
    assert controller.get_state() == GrblMachineState.ALARM

    mock_conn.get_state_raw.return_value = "Alarm"
    assert controller.get_state_raw() == "Alarm"

    callback = lambda p, s: None
    controller.set_on_state_update_callback(callback)
    mock_conn.set_on_state_update_callback.assert_called_with(callback)


def test_esp32_duino_control_commands():
    mock_conn = Mock()
    mock_conn.receive.return_value = "ok"
    controller = ESP32Duino(mock_conn)
    mock_conn.reset_mock()

    controller.killalarm()
    mock_conn.killalarm.assert_called_once()

    controller.softreset()
    mock_conn.softreset.assert_called_once()

    controller.hold()
    mock_conn.hold.assert_called_once()

    controller.shutdown()
    mock_conn.close.assert_called_once()


@patch('nfs.grbl_controller.configparser.ConfigParser')
def test_grbl_controller_factory_mock(mock_config_class):
    mock_config = mock_config_class.return_value
    mock_config.get.return_value = 'Mock'

    # Reset singleton
    GrblControllerFactory._instance = None

    controller = GrblControllerFactory.create('grbl', 'config.ini')
    assert isinstance(controller, GrblControllerMock)

    # Test singleton
    controller2 = GrblControllerFactory.create('grbl', 'config.ini')
    assert controller is controller2


def test_grbl_controller_factory_rebuilds_when_type_changes(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[grbl]\n"
        "type = Mock\n",
        encoding="utf-8",
    )
    GrblControllerFactory.reset(shutdown=False)

    controller = GrblControllerFactory.create("grbl", str(config_file))
    assert isinstance(controller, GrblControllerMock)

    config_file.write_text(
        "[grbl]\n"
        "type = MockSimulatedDRO\n"
        "mock_linear_speed_mm_s = 123.0\n"
        "mock_angular_speed_deg_s = 45.0\n"
        "mock_status_hz = 7.0\n",
        encoding="utf-8",
    )

    controller2 = GrblControllerFactory.create("grbl", str(config_file))

    assert isinstance(controller2, GrblControllerMockSimulatedDRO)
    assert controller2 is not controller


def test_grbl_controller_factory_reuses_when_signature_matches(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[grbl]\n"
        "type = MockSimulatedDRO\n"
        "mock_linear_speed_mm_s = 123.0\n"
        "mock_angular_speed_deg_s = 45.0\n"
        "mock_status_hz = 7.0\n",
        encoding="utf-8",
    )
    GrblControllerFactory.reset(shutdown=False)

    controller = GrblControllerFactory.create("grbl", str(config_file))
    controller2 = GrblControllerFactory.create("grbl", str(config_file))

    assert controller2 is controller


@patch('nfs.grbl_controller.GrblStreamer')
@patch('nfs.grbl_controller.configparser.ConfigParser')
@patch('nfs.grbl_controller.time.sleep')  # speed up test
def test_grbl_controller_factory_arduino(mock_sleep, mock_config_class, mock_streamer_class):
    mock_config = mock_config_class.return_value

    def mock_get(section, option, **_kwargs):
        if option == 'type':
            return 'Arduino'
        if option == 'port':
            return 'COM3'
        if option == 'baudrate':
            return '115200'
        return ''

    mock_config.get.side_effect = mock_get
    mock_config.getint.return_value = 115200

    # Reset singleton
    GrblControllerFactory._instance = None

    # To avoid ESP32Duino.__init__ calling _unlock -> send -> _wait_for_ack -> _receive
    # We need to mock GrblStreamer.receive or GrblStreamer.send_immediately etc.
    # But ESP32Duino uses GrblStreamerClientConnection.

    with patch('nfs.grbl_controller.ESP32Duino') as mock_esp32:
        controller = GrblControllerFactory.create('grbl', 'config.ini')
        assert controller == mock_esp32.return_value
        mock_streamer_class.return_value.cnect.assert_called()
