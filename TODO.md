- Jogging just after startup my cause very large movements, even to endstop. Check whether the position is correct and not the initial value which is 0 and should probably be none and we should enforce postiion update
- Jan showed an image where the position is 0 after " So i set a height offset of 70mm, do SET  HEIGHT OFFSET, then ZERO NFS.". I expected 70mm. After a move everything is ok again. Probably force position update after setting G54. And suppressing it when we are momentarilly in G55. Can we make get_position updates push to the iso the gui asking?
- Status seems to be IDLE too often. Even when it is moving it still shows IDLE
- make test of audio stuff using the piston script


class NfsState(str, Enum):
    NOT_HOMED = "not_homed"  # Initial state: mechanical setup is unhomed.
    IDLE = "idle"            # Ready and homed: waiting for commands.
    MOVING = "moving"        # Manual movement or positioning is in progress.
    MEASURING = "measuring"  # Automated measurement sequence is in progress.
    ALARM = "alarm"          # System is in alarm state (e.g., limit hit).

class Scanner:
    def __init__(self, grbl_controller: IGrblController, feed_rate):
        self._grbl_controller = grbl_controller
        self._feed_rate = feed_rate
        self._is_homed = False  # Track homing status
        self._grbl_controller.force_position_update()

    @property
    def is_homed(self) -> bool:
        return self._is_homed

    def home(self) -> None:
        # $H starts homing. send_and_wait_for_move_ready ensures we wait for completion.
        self._grbl_controller.send_and_wait_for_move_ready('$H')
        self._is_homed = True

    def clear_alarm(self) -> None:
        self._grbl_controller.killalarm()
        self._is_homed = False  # Position is untrusted after alarm/unlock

    def softreset(self) -> None:
        self._grbl_controller.softreset()
        self._is_homed = False  # Reset usually loses homing/sync

class NearFieldScanner:
    def __init__(self, scanner, audio, motion_manager):
        self._scanner = scanner
        self._is_measuring = False  # Internal flag for measurement sessions
        # ... other init code ...

    def get_state(self) -> NfsState:
        # Priority 1: Hardware Alarms
        if self._scanner.is_alarm():
            return NfsState.ALARM
        
        # Priority 2: Automated Measuring Session
        if self._is_measuring:
            return NfsState.MEASURING
        
        # Priority 3: Manual Movement (GRBL is not Idle)
        if not self._scanner.is_idle():
            return NfsState.MOVING
        
        # Priority 4: Stationary but not homed
        if not self._scanner.is_homed:
            return NfsState.NOT_HOMED
            
        # Priority 5: Stationary and homed
        return NfsState.IDLE

    def take_measurement_set(self) -> None:
        self._is_measuring = True
        try:
            # ... existing measurement logic ...
        finally:
            self._is_measuring = False

stateDiagram-v2
    [*] --> NOT_HOMED : System Initialization
    
    NOT_HOMED --> MOVING : home() / manual move
    MOVING --> NOT_HOMED : Move finished (unhomed)
    MOVING --> IDLE : home() finished
    
    IDLE --> MOVING : Scanner move commands
    MOVING --> IDLE : Move finished
    
    IDLE --> MEASURING : take_measurement_set()
    MEASURING --> IDLE : Measurement complete
    
    NOT_HOMED --> ALARM : GRBL Alarm (Limit hit, etc.)
    IDLE --> ALARM : GRBL Alarm
    MOVING --> ALARM : GRBL Alarm
    MEASURING --> ALARM : GRBL Alarm
    
    ALARM --> NOT_HOMED : clear_alarm() ($X)

| Function | NOT_HOMED | IDLE | MOVING | MEASURING | ALARM |
| :--- | :---: | :---: | :---: | :---: | :---: | 
| Scanner.home() | ✅ | ✅ | ❌ | ❌ | ❌* | 
| Scanner.clear_alarm() | ✅ | ✅ | ✅ | ✅ | ✅ | 
| Manual Moves (Radial, etc.) | ⚠️ | ✅ | ❌ | ❌ | ❌ | 
| Scanner.set_as_zero() | ❌ | ✅ | ❌ | ❌ | ❌ | 
| NFS.take_measurement_set() | ❌ | ✅ | ❌ | ❌ | ❌ | 
| NFS.take_single_measurement()| ❌ | ✅ | ❌ | ❌ | ❌ | 
| Scanner.hold() / Stop | ✅ | ✅ | ✅ | ✅ | ✅ | 
| Scanner.softreset() | ✅ | ✅ | ✅ | ✅ | ✅ |
