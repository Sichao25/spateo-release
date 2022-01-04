from typing import Optional, Tuple, Union

import math
import numpy as np
from shapely.geometry import Point, MultiPoint, LineString, MultiLineString, Polygon
from scipy.spatial import Delaunay
from shapely.ops import unary_union, polygonize

from .bgi import read_bgi_agg


def alpha_shape(
    x: np.ndarray,
    y: np.ndarray,
    alpha: float = 1,
    buffer: float = 1,
) -> Tuple[Polygon, list]:
    """Compute the alpha shape (concave hull) of a set of points.
    Code adapted from: https://gist.github.com/dwyerk/10561690

    Args:
        x: x-coordinates of the DNA nanoballs or buckets, etc.
        y: y-coordinates of the DNA nanoballs or buckets, etc.
        alpha: alpha value to influence the gooeyness of the border. Smaller
                  numbers don't fall inward as much as larger numbers. Too large,
                  and you lose everything!
        buffer: the buffer used to smooth and clean up the shapley identified concave hull polygon.

    Returns:
        alpha_hull: The computed concave hull.
        edge_points: The coordinates of the edge of the resultant concave hull.
    """

    crds = np.array([x.flatten(), y.flatten()]).transpose()
    points = MultiPoint(crds)

    if len(points) < 4:
        # When you have a triangle, there is no sense
        # in computing an alpha shape.
        return MultiPoint(list(points)).convex_hull

    def add_edge(edges, edge_points, coords, i, j):
        """
        Add a line between the i-th and j-th points,
        if not in the list already
        """
        if (i, j) in edges or (j, i) in edges:
            # already added
            return
        edges.add((i, j))
        edge_points.append(coords[[i, j]])

    coords = np.array([point.coords[0] for point in points])

    tri = Delaunay(coords)
    edges = set()
    edge_points = []

    # loop over triangles:
    # ia, ib, ic = indices of corner points of the triangle
    for ia, ib, ic in tri.vertices:
        pa = coords[ia]
        pb = coords[ib]
        pc = coords[ic]

        # Lengths of sides of triangle
        a = math.sqrt((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2)
        b = math.sqrt((pb[0] - pc[0]) ** 2 + (pb[1] - pc[1]) ** 2)
        c = math.sqrt((pc[0] - pa[0]) ** 2 + (pc[1] - pa[1]) ** 2)

        # Semiperimeter of triangle
        s = (a + b + c) / 2.0

        # Area of triangle by Heron's formula
        area = math.sqrt(s * (s - a) * (s - b) * (s - c))
        circum_r = a * b * c / (4.0 * area)

        # Here's the radius filter.
        if circum_r < 1.0 / alpha:
            add_edge(edges, edge_points, coords, ia, ib)
            add_edge(edges, edge_points, coords, ib, ic)
            add_edge(edges, edge_points, coords, ic, ia)

    triangles = list(polygonize(edge_points))
    alpha_hull = unary_union(triangles)

    if buffer != 0:
        alpha_hull.buffer(buffer)

    return alpha_hull, edge_points


def in_convex_hull(
    p: np.ndarray, convex_hull: Union[Delaunay, np.ndarray]
) -> np.ndarray:
    """Test if points in `p` are in `convex_hull` using scipy.spatial Delaunay's find_simplex.

    Args:
        p: a `NxK` coordinates of `N` points in `K` dimensions
        convex_hull: either a scipy.spatial.Delaunay object or the `MxK` array of the coordinates of `M` points in `K`
              dimensions for which Delaunay triangulation will be computed.

    Returns:

    """
    assert (
        p.shape[1] == convex_hull.shape[1]
    ), "the second dimension of p and hull must be the same."

    if not isinstance(convex_hull, Delaunay):
        hull = Delaunay(convex_hull)

    return hull.find_simplex(p) >= 0


def in_concave_hull(p: np.ndarray, concave_hull: Polygon) -> np.ndarray:
    """Test if points in `p` are in `concave_hull` using scipy.spatial Delaunay's find_simplex.

    Args:
        p: a `Nx2` coordinates of `N` points in `K` dimensions
        concave_hull: A polygon returned from the concave_hull function (the first value).

    Returns:

    """
    assert p.shape[1] == 2, "this function only works for two dimensional data points."

    res = [concave_hull.intersects(Point(i)) for i in p]

    return np.array(res)


def get_concave_hull(
    path: str,
    min_agg_umi: int = 0,
    alpha: float = 1,
    buffer: float = 1,
) -> Tuple[Polygon, list]:
    """Return the convex hull of all nanoballs that have non-zero UMI (or at least > x UMI).

    Args:
        path: Path to read file.
        min_agg_umi: the minimal aggregated UMI number for the bucket.
        alpha: alpha value to influence the gooeyness of the border. Smaller
                  numbers don't fall inward as much as larger numbers. Too large,
                  and you lose everything!
        buffer: the buffer used to smooth and clean up the shapley identified concave hull polygon.

    Returns:
        alpha_hull: The computed concave hull.
        edge_points: The coordinates of the edge of the resultant concave hull.
    """
    total_agg = read_bgi_agg(path)[0]

    # We may need to use the true coordinates (instead of the indices) from the bgi data input.
    # So read_bgi_agg may be need to return those values.
    i, j = (total_agg > min_agg_umi).nonzero()

    return alpha_shape(i, j, alpha, buffer)
