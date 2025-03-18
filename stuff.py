
# def has_intersect(plane_normal, ray_direction, epsilon=1e-6) -> bool:
#     """
#     Determines whether a ray intersects with a plane. The calculation is based on the dot
#     product of the plane's normal vector and the ray's direction vector. If the absolute
#     value of this dot product is less than a small threshold (epsilon), the ray is
#     considered parallel to the plane, and thus does not intersect.
#
#     :param plane_normal: A vector representing the normal of the plane.
#     :type plane_normal: numpy.ndarray
#     :param ray_direction: A vector representing the direction of the ray.
#     :type ray_direction: numpy.ndarray
#     :param epsilon: A very small value used as a threshold to determine if the dot
#         product is sufficiently close to zero to consider the ray parallel.
#     :type epsilon: float
#     :return: ``True`` if an intersection occurs; otherwise, ``False``.
#     :rtype: bool
#     """
#     n_dot_u = plane_normal.dot(ray_direction)
#     if abs(n_dot_u) < epsilon:
#         return False
#
#     return True
#
#
# def line_plane_intersection(plane_normal, plane_point, ray_direction, ray_point) -> np.array:
#     """
#     Calculates the intersection point of a ray and a plane defined in 3D space. The function
#     performs the computation based on vector mathematics and returns the intersection
#     coordinate as a NumPy array. If the ray is parallel to the plane and does not intersect,
#     the results may be undefined.
#
#     :param plane_normal: The normal vector of the plane, defining its orientation.
#     :param plane_point: A point on the plane used to define its position in 3D space.
#     :param ray_direction: The direction vector of the ray, indicating its trajectory.
#     :param ray_point: A point on the ray representing its position.
#     :return: A NumPy array representing the coordinate of the intersection point.
#     """
#     n_dot_u = plane_normal.dot(ray_direction)
#
#     w = ray_point - plane_point
#     si = -plane_normal.dot(w) / n_dot_u
#     psi = w + si * ray_direction + plane_point
#     return psi
#
# def is_between(a, b, c):
#     """
#     Determines whether a given value `b` lies between two other values, `a` and
#     `c`. The function checks the order of `a` and `c`, allowing for cases where
#     `a` might be greater than `c` or where `a` is less than or equal to `c`.
#
#     :param a: The first boundary value in the comparison.
#     :type a: int or float
#     :param b: The value to check if it lies between `a` and `c`.
#     :type b: int or float
#     :param c: The second boundary value in the comparison.
#     :type c: int or float
#     :return: A boolean indicating if `b` lies between `a` and `c` inclusively.
#     :rtype: bool
#     """
#     if a >= c:
#         return a >= b >= c
#
#     return a <= b <= c
#
# def is_vertical_move_safe(current_position, next_position, plane_point_z):
#     """
#     Determines if a vertical movement between two positions is safe given constraints related to
#     a predefined plane. The function checks whether the path between the two positions intersects
#     the plane, and if the potential intersection point lies within specific boundaries.
#
#     :param current_position: The starting position of the movement.
#     :param next_position: The intended end position of the movement.
#     :param plane_point_z: The z-coordinate of a fixed plane against which the movement's safety
#         is evaluated.
#     :return: A boolean value indicating whether the vertical move is safe (True) or unsafe (False).
#     """
#     plane_normal = np.array([0, 0, 1])
#     move_direction = np.array([0, 0, 1])
#     plane_point = np.array([0, 0, plane_point_z])
#
#     x, y, z = cyl_to_cart(current_position)
#     move_point = np.array([x, y, z])
#
#     intersection_point = line_plane_intersection(plane_normal, plane_point, move_direction, move_point)
#
#     intersect_is_during_move = is_between(current_position.z(), intersection_point[2], next_position)
#     if abs(intersection_point[0]) < 270 / 2 and abs(
#             intersection_point[1]) < 195 / 2 and intersect_is_during_move:  # TODO from config
#         logger.info(
#             f'Unsafe move requested from {x, y, z} [mm] to {x, y, next_position} [mm] (xyz). Reverting to evasive move')
#         return False
#
#     return True
#
#
# def is_radial_move_safe(current_position, next_position):
#     """
#     Check if a radial move is safe based on the current and next positions.
#
#     The function evaluates whether the move from the given current position
#     to the specified next position is within the acceptable radial and positional
#     boundaries. If the move is deemed unsafe, it logs the occurrence and triggers
#     a revert to an evasive maneuver.
#
#     :param current_position: The current position of the object with coordinates
#                              including a z-component.
#     :type current_position: Any
#     :param next_position: The next calculated radial position to evaluate.
#     :type next_position: float
#     :return: A boolean value indicating if the radial move is safe
#              (True if safe, False otherwise).
#     :rtype: bool
#     """
#     radius = np.sqrt((195 / 2) ** 2 + (270 / 2) ** 2)
#     if np.abs(current_position.z()) <= 375 / 2 and next_position < radius:
#         logger.info('Unsafe radial move requested. Reverting to evasive move')
#         return False
#
#     return True
