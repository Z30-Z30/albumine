"""Shared test configuration.

The default volume paths (``/input`` etc.) only exist inside the container.
Point them at a temporary directory before any app code reads the settings.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="albumine-test-")
os.environ.setdefault("ALBUMINE_INPUT_DIR", os.path.join(_tmp, "input"))
os.environ.setdefault("ALBUMINE_OUTPUT_DIR", os.path.join(_tmp, "output"))
os.environ.setdefault("ALBUMINE_CONFIG_DIR", os.path.join(_tmp, "config"))
