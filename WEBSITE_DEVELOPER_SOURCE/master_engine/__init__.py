"""Standalone Master Editor.

This package deliberately does not import or call the legacy editing pipeline.
"""

__version__ = "7.8.1"

from .hotfix_v71 import install as _install_v71
_install_v71()
del _install_v71

from .hotfix_v72 import install as _install_v72
_install_v72()
del _install_v72

from .hotfix_v73 import install as _install_v73
_install_v73()
del _install_v73

from .hotfix_v74 import install as _install_v74
_install_v74()
del _install_v74

from .hotfix_v75 import install as _install_v75
_install_v75()
del _install_v75

from .hotfix_v76 import install as _install_v76
_install_v76()
del _install_v76

from .hotfix_v77 import install as _install_v77
_install_v77()
del _install_v77

from .hotfix_v78 import install as _install_v78
_install_v78()
del _install_v78

# MASTER_FRAME_CONTROL_V781
from .hotfix_v79_frames import install as _install_v79_frames
_install_v79_frames()
del _install_v79_frames

from .engine import MasterEngine, MasterEngineError

__all__ = ["MasterEngine", "MasterEngineError"]
