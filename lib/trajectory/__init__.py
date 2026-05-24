"""J3 trajectory shipper package.

Public surface kept narrow: the shipper class and the exception that
triggers F37 dispatch. Internal helpers are not re-exported.
"""

from lib.trajectory.shipper import (
    ModelArmorSanitizeUnavailable,
    TrajectoryShipper,
)

__all__ = ["ModelArmorSanitizeUnavailable", "TrajectoryShipper"]
