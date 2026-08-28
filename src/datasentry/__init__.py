"""DataSentry AI — SDK client and package version."""

from importlib.metadata import PackageNotFoundError, version

from datasentry.client import DataSentry

try:
    __version__ = version("datasentry-ai")
except PackageNotFoundError:  # source-tree imports before package installation
    __version__ = "0+unknown"

__all__ = ["DataSentry", "__version__"]
