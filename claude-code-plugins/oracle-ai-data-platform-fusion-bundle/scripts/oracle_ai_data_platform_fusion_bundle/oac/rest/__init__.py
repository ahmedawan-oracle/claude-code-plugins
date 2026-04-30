"""OAC REST API wrappers used by ``aidp-fusion-bundle dashboard install --target oac``.

Public API:
    * :class:`IdcsTokenFetcher` — OAuth client-credentials Bearer token fetcher
    * :class:`AidpConnectionPayload` / :func:`build_payload` / :func:`render_template`
      — build and serialize the 6-key AIDP JDBC connection JSON
    * :class:`OacRestClient` — Bearer-authenticated wrapper for
      ``/api/<v>/catalog/connections`` and ``/api/<v>/catalog/workbooks/imports``
"""

from .client import OacRestClient, OacRestError
from .connection import AidpConnectionPayload, build_dsn, build_payload, render_template
from .oauth import IdcsTokenFetcher, TokenBundle

__all__ = [
    "OacRestClient",
    "OacRestError",
    "AidpConnectionPayload",
    "build_dsn",
    "build_payload",
    "render_template",
    "IdcsTokenFetcher",
    "TokenBundle",
]
