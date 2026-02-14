# TODO

things to do:

- make it easy to see if the esp32's are pingable
- make it easy to see if the esp32's are connected (socket ok)
- turn off power to rotation servo (heat & noise)
- performing a measurement right after 'setting to zero' still shows old position
- Look at https://www.klippel.de/manuals/docs/directivity-roomcorrection/nfs/nfs.html for a detailed view of what NFS does. Full of ideas
- make coordinate systems more explicit, spherical, cylindrical, mechanics origin, acoustics origin, ...
This is probably why I can't set the direction of the TMC2209:
GrblStreamerClientConnection: Sending message: $/axes/y/motor0/stepstick/direction_pin=gpio.27:high
MY CALLBACK: event=on_write                       data=$/axes/y/motor0/stepstick/direction_pin=gpio.27:high
MY CALLBACK: event=on_read                        data=Runtime setting of Pin objects is not supported <-- NOT SUPPORTED!!!!


Cylindrical moves for cylindrical measurement points.

For spherical measurement points we can use the arc motion provided in grbl to move over the surface of the sphere.

I would like to use that same idea for the cylindrical measurement points. Than we can simply check that the speaker is fully inside the cylinder.
And all consecutive moves are over the cylinder surface and thus safe to perform.
For spherical measurement points, the radius fully describes the sphere. Center point is assumed in the center of everything. This radius can always
be calculated from the measurement point alone.
For cylindrical surface we need both the radius and height is needed. Somehow we need to know whether we are on the side or on the top/bottom cap.
We could introduce a motion planner. For the spherical measurement points we could use a simple arc motion.

endstop -> soft reset -> clear alarm -> home



Downward motion:
top cap -> top cap : radius move only
top cap -> side wall : radius move to cylinder radius, move down
top cap -> bottom cap : radius move to cylinder radius, move down, radius move in
side wall -> side wall : move down
side wall -> bottom cap : move down, move in

Upward motion:
in reverse direction as downward motion

