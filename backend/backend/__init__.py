"""Deployment import shim for backend-root Railway builds.

When Railway uses ``backend/`` as the service root, the package modules live
beside this directory (``api/``, ``services/``, etc.) rather than under another
``backend/`` parent. Extending ``__path__`` keeps existing ``backend.*`` imports
working without changing application modules.
"""

from pathlib import Path

__path__.append(str(Path(__file__).resolve().parent.parent))
