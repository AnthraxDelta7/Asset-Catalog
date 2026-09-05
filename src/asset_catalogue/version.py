"""Single source of truth for the app's version number.

Read by pyproject.toml (via setuptools' dynamic version, so packaging
metadata never drifts from this) and by the running app itself -- for
display (About) and for the update checker to compare against GitHub's
latest release tag. Bump this, and only this, to cut a new release.
"""

__version__ = "0.2.0"
