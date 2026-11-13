"""Deprecation marker shared by the package.

``deprecated`` is the PEP 702 decorator. ``typing_extensions`` re-exports
``warnings.deprecated`` on Python 3.13+ and provides the same decorator on older versions,
so one import serves every supported interpreter.
"""

from typing_extensions import deprecated

__all__ = ["deprecated"]
