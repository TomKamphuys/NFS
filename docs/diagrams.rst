Architecture Diagrams
======================

This document contains the architecture diagrams for the Near Field Scanner (NFS) system, formatted using `Mermaid <https://mermaid.js.org/>`_.

Class Diagram
-------------

The following class diagram reflects the current structure of the codebase, including methods and relationships.

.. mermaid::

    classDiagram
        class NearFieldScanner {
            - _scanner: Scanner
            - _audio: IAudio
            - _measurement_motion_manager: IMotionManager
            - _position_log_file: str
            + take_single_measurement()
            + take_measurement_set()
            + shutdown()
            - _clear_position_log()
            - _append_position_to_file(position)
        }

        class NearFieldScannerFactory {
            + create(scanner: Scanner, config_file: str)$ NearFieldScanner
        }

        class Scanner {
            - _grbl_controller: IGrblController
            - _feed_rate: float
            + planar_move_to(r, z)
            + radial_move_to(r)
            + angular_move_to(angle)
            + vertical_move_to(z)
            + cw_arc_move_to(r, z, radius)
            + ccw_arc_move_to(r, z, radius)
            + rotate_ccw(amount)
            + rotate_cw(amount)
            + move_out(amount)
            + move_in(amount)
            + move_up(amount)
            + move_down(amount)
            + get_position() CylindricalPosition
            + get_state() GrblMachineState
            + is_idle() bool
            + is_running() bool
            + is_alarm() bool
            + set_as_zero()
            + set_speaker_center_above_stool(height)
            + home()
            + clear_alarm()
            + softreset()
            + hold()
            + shutdown()
        }

        class IMotionManager {
            <<abstract>>
            + move_to_safe_starting_radius()*
            + next()* CylindricalPosition
            + ready()* bool
            + reset()*
            + shutdown()*
            + total_points()* int
        }

        class MeasurementPoints {
            <<Protocol>>
            + next() CylindricalPosition
            + ready() bool
            + get_radius() float
            + reset()
            + need_to_do_evasive_move() bool
            + total_points() int
        }

        class IGrblController {
            <<abstract>>
            + send(message)*
            + send_and_wait_for_move_ready(message)*
            + killalarm()*
            + softreset()*
            + hold()*
            + get_position()* CylindricalPosition
            + get_state()* GrblMachineState
            + get_state_raw()* str
            + force_position_update()*
            + shutdown()*
        }

        class IAudio {
            <<abstract>>
            + measure_ir(position, order_id)*
        }

        class CylindricalPosition {
            - _r: float
            - _t: float
            - _z: float
            + r() float
            + t() float
            + z() float
            + set_r(r)
            + set_t(t)
            + set_z(z)
            + length() float
        }

        NearFieldScanner --> Scanner
        NearFieldScanner --> IAudio
        NearFieldScanner --> IMotionManager
        NearFieldScannerFactory ..> NearFieldScanner
        Scanner --> IGrblController
        ScannerFactory ..> Scanner
        IMotionManager <|.. CylindricalMeasurementMotionManager
        IMotionManager <|.. SphericalMeasurementMotionManager
        CylindricalMeasurementMotionManager --> MeasurementPoints
        SphericalMeasurementMotionManager --> MeasurementPoints
        IGrblController <|.. ESP32Duino
        IGrblController <|.. GrblControllerMock
        IAudio <|.. Audio
        IAudio <|.. AudioMock

Sequence Diagram: Measurement Set
---------------------------------

This diagram illustrates the process of taking a full set of measurements. The ``NearFieldScanner`` coordinates between the ``IMotionManager``, ``Scanner`` and ``IAudio`` components.

.. mermaid::

    sequenceDiagram
        autonumber
        participant NFS as NearFieldScanner
        participant MM as IMotionManager
        participant S as Scanner
        participant A as IAudio

        NFS->>NFS: _clear_position_log()
        NFS->>MM: move_to_safe_starting_radius()
        NFS->>MM: total_points()
        
        loop Measurement loop
            NFS->>MM: next()
            MM->>S: move_to(...)
            Note over MM, S: Scanner movement commands
            NFS->>MM: ready()
            break if ready
                Note over NFS: All points measured
            end
            NFS->>S: get_position()
            S-->>NFS: current_position
            NFS->>NFS: _append_position_to_file(...)
            NFS->>A: measure_ir(position)
            A-->>NFS: return
        end
        
        NFS->>MM: reset()
        NFS->>MM: move_to_safe_starting_radius()
        NFS->>S: angular_move_to(0.0)

Sequence Diagram: Scanner Move
------------------------------

This diagram shows how a movement command flows from the ``Scanner`` to the ``IGrblController`` and how it waits for completion.

.. mermaid::

    sequenceDiagram
        autonumber
        participant S as Scanner
        participant GC as IGrblController

        S->>S: get_position()
        S->>GC: get_position()
        GC-->>S: current_position
        
        alt position != requested
            S->>GC: send_and_wait_for_move_ready("G0 ...")
            activate GC
            GC->>GC: send("G0 ...")
            loop while state != IDLE
                GC->>GC: force_position_update()
                GC->>GC: get_state()
            end
            deactivate GC
        end
        S-->>S: Done
