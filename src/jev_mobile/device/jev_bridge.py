"""Named compatibility backend for the retained first-party Android bridge."""

from .accessibility_adb import AccessibilityAdbAdapter

JevBridgeDeviceAdapter = AccessibilityAdbAdapter

__all__ = ["JevBridgeDeviceAdapter"]
