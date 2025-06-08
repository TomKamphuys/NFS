# TODO

things to do:

- make it easy to see if the esp32's are pingable
- make it easy to see if the esp32's are connected (socket ok)
- invert the up/down direction. Config file does not seem to work -> set as homing direction, that is something different
  - Swap Direction
    - Option 1 - Swap a pair of wires in the stepper cable - like this
    - Option 2 - In the yaml config file, find the direction_pin: for the axis that is going backwards. Either add or remove :low from the value. For example, if it says direction_pin: gpio.12, change that to direction_pin: gpio.12:low. Conversely, it if already says gpio.12:low, change it to gpio.12.
- Control rotational motion from web UI also (not needed if motor is de-energized)
- turn off power to rotation servo (heat & noise)
http://wiki.fluidnc.com/en/config/axes#idle_ms
idle_ms:
Type: Integer
Range: 0-255
Default: 250
Details: A value of 255 will keep the motors enabled at all times (preferred for most projects). Any value between 0-254 will disable all the motors that many milliseconds after the last step on any motor. Note: Motors can be manually disabled at any time with the $MD command.
- performing a measurement right after 'setting to zero' still shows old position