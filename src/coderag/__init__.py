"""CodeRAG: token-efficient, structure-aware Code Intelligence + RAG."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    # Distribution name is `coderag-ai`; the import name is `coderag`.
    __version__ = _version("coderag-ai")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
