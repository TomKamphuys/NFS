# Motion preview

Use the **View: 2D / 3D** selector in Motion Preview to switch between the
original radius/height projection and a Cartesian 3D view. 2D is the default.
In 3D, drag with the left mouse button to rotate, middle-drag to pan, and
right-drag to zoom. Axes are in millimetres with equal physical scaling.

Both views show the planned trajectory, measurement points, travelled path,
current arm position, and configured no-fly zone. The 3D zone is a transparent
closed cylinder or sphere. Unsafe points retain their flashing warning markers.
Switching views preserves the playback position and play/pause state.

The 3D trajectory includes angular motion and simultaneous R/theta/Z moves,
not just lines between measurement points. Rotation follows the commanded
angle without wrapping across the -180/180 boundary. Playback advances through
sampled positions; it is not a prediction of elapsed machine time. This is a
position/path preview, not a solid model of the entire scanner assembly.