# How the balanced measurement grid works

## The aim

The balanced constructor places microphone positions around the DUT so that the
measurement grid covers the directions seen from the cylinder centre, while
allowing for the greater distance to some parts of the cylinder.

It aims to give the spherical harmonic solver a useful spread of measurements
around the DUT. It also spreads the points through a shell of finite thickness,
so they are not all measured at one distance.

The cylinder dimensions still define the space needed to clear the DUT. The
constructor changes the placement of points within the allowed measurement shell.

## Why equal spacing on a cylinder is not equal angular coverage

Imagine standing at the centre of the measurement cylinder and looking at its
sidewall.

A small patch directly beside you looks larger than an equally sized patch near
the top or bottom. The distant patch is both farther away and seen at a more
oblique angle. It occupies less of your view.

Consequently, placing the same number of points in every equal-sized wall patch
does not give an even spread of viewing directions from the centre.

This is the distinction between:

- **Surface coverage:** how closely points are spaced on the physical cylinder.
- **Angular coverage:** how closely their directions are spaced when viewed from
  the centre.

Here, a direction means the line from the design centre to a microphone. It does
not necessarily describe the direction of the local sound wavefront from a
complex DUT.

## Why the new constructor is called balanced

One possible approach is to distribute directions evenly around a sphere and
project them onto the cylinder. This gives uniform angular coverage, but it
concentrates points around the middle of the sidewall and leaves noticeably fewer
near the ends.

The balanced constructor adjusts that approach. It gives more sampling weight to
directions that travel farther before reaching the measurement shell. The reason
is that sound generally becomes weaker with distance, so a purely angular layout
can leave distant regions relatively underrepresented in the measured field.

The result is a compromise:

- More attention to angular coverage than a uniform surface layout.
- Less thinning near the wall ends than a purely uniform angular layout.
- Continued coverage of the upper and lower parts of the cylinder and both caps.

This is a rule for placing microphones. It does not increase recorded signal
levels, apply gain corrections, or change the weighting used by the solver.
The distance compensation is an approximation; a real driver's directivity and
sound field can be more complicated.

## How a point is placed

The constructor repeats the following process until it has enough valid points.

### 1. Choose a direction

It selects a direction looking outward from the cylinder centre. The selection
follows the balanced angular distribution described above.

If front azimuth weighting is enabled, more directions are assigned toward the
chosen front of the DUT. This is applied while selecting points, rather than by
squeezing a finished grid afterwards. In this constructor it applies to cap
positions as well as sidewall positions.

### 2. Find where that direction passes through the shell

Imagine drawing a straight ray from the centre along the selected direction.
The constructor finds where it leaves the inner clearance cylinder and where
it leaves the outer measurement boundary.

The section between these crossings is available for placing a microphone.
Depending on the direction, it can lie near a sidewall, an end cap, or a corner.

### 3. Choose a distance within that section

The microphone position is placed somewhere along that available section.
Different points receive different distances, filling the shell rather than
forming a few discrete layers.

The thickness setting still specifies how far the outer cylinder extends beyond
the inner cylinder: radially at the wall and vertically at each cap. Point
placement within that shell follows rays from the centre. It is not the legacy
method of first placing a surface point and then pushing it directly outward.

### 4. Make it a usable robot position

The position is rounded to the robot-coordinate resolution:

- Radius and height: nearest millimetre.
- Azimuth: nearest tenth of a degree.

The constructor checks the rounded position against the allowed boundaries,
azimuth limits and bottom support exclusion. Duplicate positions are discarded.
It continues drawing candidates until the requested number of valid, distinct
points is reached, or reports that it cannot fill the request within its draw
limit.

## Why the pattern looks irregular but is repeatable

The constructor uses a deterministic sampling sequence designed to spread its
samples across the available choices of direction and distance.

It looks less like a spiral or a set of rings than the legacy grid, but it is not
an unrepeatable random cloud. The same settings and seed reproduce the same grid
in the same software environment.

Separate parts of the sequence choose elevation, azimuth and distance. This helps
avoid obvious repeating relationships between angle and depth. It does not
promise perfect spacing or the lowest possible mathematical coherence.

## What to expect in the point-cloud viewer

The balanced grid will usually show a gentle reduction in sidewall point density
near the upper and lower ends. The reduction is weaker than with pure uniform
angular coverage, because the balancing rule adds weight back toward more
distant directions.

For a R200 mm / H500 mm comparison with a 50 mm shell, equal-width height bands  
near the wall ends contained about 72% as many points as the legacy grid which was even across the side walls.

## Which settings matter

| Setting | Effect on balanced generation |
|---|---|
| Cylinder radius and height | Set the internal clearance around the DUT. |
| Wall thickness | Sets the outward extent of the measurement shell. |
| Point count | Sets the requested number of valid microphone positions. |
| Azimuth limits | Restrict the allowed rotation range. |
| Front azimuth weighting | Concentrates directions toward the selected front. |
| Bottom cutoff | Keeps positions clear of the support near the lower cap. |
| Seed | Selects a repeatable sampling pattern. |
| Cap fraction: Auto | Lets the balanced distribution allocate wall/cap directions. |
| Cap fraction: manual | Overrides the nominal allocation; exclusions can change the final proportions. |
| Z midpoint / waypoint datum | Places the cylinder in the robot's coordinate system. |

The legacy reverse spiral, pole flipping, spiral rotation, P Side and P Caps
controls do not drive this constructor. Balanced generation already has its own
way of distributing both directions and distances.

## Related results

- [Grid geometry and matrix comparisons](balanced_vs_legacy/README.md)
- [30 mm directional-source solve comparison](balanced_vs_legacy/piston30/README.md)
