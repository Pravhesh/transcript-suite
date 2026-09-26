"""
Storage management and retention module for Transcript Suite.
"""

from .retention import RetentionManager, get_retention_manager

__all__ = ["RetentionManager", "get_retention_manager"]
