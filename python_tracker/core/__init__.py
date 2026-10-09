"""Core configuration and gun state management module."""

from . import config
from .gun_state import GunState, MultiGunManager

__all__ = ["config", "GunState", "MultiGunManager"]
