"""Smoke test for the scikit-geometry straight-skeleton binding.

This project needs ``sg.skeleton.create_interior_straight_skeleton`` in
``data_myMeshes.py``. Some manual builds of ``skgeom`` import correctly but
fail at that binding with a pybind11 argument-conversion error.
"""

from __future__ import annotations

import sys


MIN_THICKNESS = 0.03 / (6**0.5)


def main() -> int:
    try:
        import numpy as np
        import skgeom as sg
    except Exception as exc:
        print(f"Failed to import numpy/skgeom: {exc!r}", file=sys.stderr)
        return 1

    print(f"numpy {np.__version__}")
    print(f"skgeom {getattr(sg, '__version__', 'unknown')}")

    points = [
        sg.Point2(0, 0),
        sg.Point2(1, 0),
        sg.Point2(1, 1),
        sg.Point2(0, 1),
    ]
    polygon = sg.Polygon(points)
    print(f"polygon area {float(polygon.area())}")

    try:
        skeleton = sg.skeleton.create_interior_straight_skeleton(polygon)
        offset_polygons = skeleton.offset_polygons(MIN_THICKNESS)
    except TypeError as exc:
        print(
            "scikit-geometry straight-skeleton binding failed. "
            "This usually means skgeom was built with an incompatible "
            "pybind11/numpy combination. Use the conda-forge environment in "
            "environment.yml, keep numpy<2 for skgeom 0.1.2, then rerun this "
            f"test.\nOriginal error: {exc!r}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"Straight-skeleton smoke test failed: {exc!r}", file=sys.stderr)
        return 1

    print(f"skeleton vertices {len(list(skeleton.vertices))}")
    print(f"offset polygons {len(offset_polygons)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
