"""llm-cockpit — multi-user web cockpit for Ollama.

Public framing per ADR-003. One Python process serves a bundled Next.js static
frontend, talks to one Ollama daemon, and stores everything in one SQLite file.
"""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("llm-cockpit")
except PackageNotFoundError:
    __version__ = "0.0.0+local"
