"""
DevSense — Changed-File Content Retrieval Layer
================================================
Retrieves complete text contents for changed files modified in a GitHub Pull Request,
pinned deterministically to the PR head commit SHA.

Security & Safety:
  - Uses the PR head commit SHA as reference (never the repository's moving default branch).
  - Skips deleted/removed files without making unnecessary GitHub API requests.
  - Skips binary and non-text files based on extension.
  - Safely handles oversized files, 404s, and API failures.
  - Never silently replaces failed retrievals with empty strings.
  - Never logs credentials, access tokens, or raw source contents.
  - Never fetches the entire repository.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from app.domain.models import ChangedFile
from app.integrations.github.client import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubClientError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubPermissionError,
)

logger = logging.getLogger("github_content")

_HEX_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")

_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".tiff",
        ".tif",
        ".psd",
        ".ai",
        ".eps",
        # Media
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".mkv",
        ".flv",
        ".webm",
        # Archives & Compressed
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".7z",
        ".rar",
        ".xz",
        # Binaries & Executables
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".o",
        ".a",
        # Bytecode & Compiled
        ".pyc",
        ".pyo",
        ".pyd",
        ".class",
        ".jar",
        ".war",
        # Fonts
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        # Databases & binary data
        ".db",
        ".sqlite",
        ".sqlite3",
        ".parquet",
        ".avro",
        # Documents (binary)
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    }
)


class FileContentStatus(str, Enum):
    """Status classification for a changed file's content retrieval."""

    SUCCESS = "SUCCESS"
    DELETED = "DELETED"
    BINARY = "BINARY"
    OVERSIZED = "OVERSIZED"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


@dataclass(frozen=True)
class FileContentResult:
    """
    Result of content retrieval for a single changed file.

    Guarantees:
      - content is a non-None string IF AND ONLY IF status == SUCCESS.
      - Failed, deleted, or binary files never have an empty-string surrogate content.
    """

    path: str
    content: str | None
    status: FileContentStatus
    error: str | None = None
    size_bytes: int | None = None

    @property
    def is_success(self) -> bool:
        return self.status == FileContentStatus.SUCCESS and self.content is not None


@dataclass
class ChangedFileContentBatch:
    """
    Aggregated container for a batch of retrieved changed files.

    Provides convenient dictionary access for downstream stages while preserving
    full failure reason auditability.
    """

    results: dict[str, FileContentResult] = field(default_factory=dict)

    @property
    def contents(self) -> dict[str, str]:
        """
        Map of file_path -> content for successfully retrieved text files only.

        Failed, deleted, or binary files are excluded from this map and are never
        represented as empty strings.
        """
        return {
            path: res.content
            for path, res in self.results.items()
            if res.is_success and res.content is not None
        }

    @property
    def failures(self) -> dict[str, str]:
        """Map of file_path -> reason for files that were not retrieved."""
        return {
            path: (res.error or res.status.value)
            for path, res in self.results.items()
            if not res.is_success
        }

    def get(self, path: str, default: str | None = None) -> str | None:
        """Get content for file_path if successfully retrieved, else default."""
        res = self.results.get(path)
        if res is not None and res.is_success:
            return res.content
        return default

    def __getitem__(self, path: str) -> str:
        """Get content for file_path, raising KeyError if not successfully retrieved."""
        res = self.results.get(path)
        if res is None or not res.is_success or res.content is None:
            raise KeyError(f"Content not available for file '{path}'")
        return res.content

    def __contains__(self, path: str) -> bool:
        res = self.results.get(path)
        return res is not None and res.is_success

    def __len__(self) -> int:
        return len(self.results)


class GitHubFileContentService:
    """
    Service for retrieving full file contents of PR changed files at a specific commit.

    Operational Invariants:
    1. Content is always retrieved using the PR head commit SHA as ref, not the moving default branch.
    2. Only eligible text files in the changed-file list are retrieved; never the full repository.
    3. Deleted/removed files are identified from metadata and skipped without API requests.
    4. Unsupported binary files are identified by extension and skipped without API requests.
    5. Failed retrievals are never silently replaced with empty strings.
    6. Raw source contents, tokens, and secrets are never logged.
    """

    def __init__(
        self,
        client: GitHubClient | None = None,
        *,
        max_file_size_bytes: int = 1_000_000,
    ) -> None:
        self._client = client
        self.max_file_size_bytes = max_file_size_bytes

    def retrieve_contents(
        self,
        repository: str,
        head_commit_sha: str,
        changed_files: Iterable[ChangedFile | dict[str, Any]],
        client: GitHubClient | None = None,
    ) -> ChangedFileContentBatch:
        """
        Retrieve complete contents for eligible changed files at the PR head commit.

        Args:
            repository: Repository in 'owner/repo' format.
            head_commit_sha: Full or short commit SHA representing the PR head.
            changed_files: Iterable of ChangedFile domain models or dictionary records.
            client: Optional GitHubClient override.

        Returns:
            ChangedFileContentBatch containing individual FileContentResult items.

        Raises:
            ValueError: If repository, head_commit_sha, or client parameters are invalid.
        """
        effective_client = client or self._client
        if effective_client is None:
            raise ValueError("A GitHubClient must be provided to retrieve contents")

        if not isinstance(repository, str) or not repository.strip():
            raise ValueError("repository must be a non-empty string")
        repo_clean = repository.strip()
        if "/" not in repo_clean:
            raise ValueError("repository must be in 'owner/repo' format containing '/'")
        owner, repo_name = (part.strip() for part in repo_clean.split("/", 1))
        if not owner or not repo_name:
            raise ValueError("repository must be in 'owner/repo' format with non-empty parts")

        if not isinstance(head_commit_sha, str) or not head_commit_sha.strip():
            raise ValueError("head_commit_sha must be a non-empty string")
        sha_clean = head_commit_sha.strip()
        if not _HEX_SHA_PATTERN.fullmatch(sha_clean):
            raise ValueError(
                f"head_commit_sha '{head_commit_sha}' must be a hexadecimal SHA between 7 and 40 characters"
            )

        batch = ChangedFileContentBatch()

        for item in changed_files:
            path, status = self._extract_path_and_status(item)
            if not path:
                continue

            # Check 1: Deleted file
            if status.lower() in ("removed", "deleted"):
                batch.results[path] = FileContentResult(
                    path=path,
                    content=None,
                    status=FileContentStatus.DELETED,
                    error=f"File '{path}' was removed/deleted in change",
                )
                logger.info("Skipping deleted file %s", path)
                continue

            # Check 2: Binary / unsupported file
            _, ext = os.path.splitext(path.lower())
            if ext in _BINARY_EXTENSIONS:
                batch.results[path] = FileContentResult(
                    path=path,
                    content=None,
                    status=FileContentStatus.BINARY,
                    error=f"File '{path}' has unsupported binary extension '{ext}'",
                )
                logger.info("Skipping binary file %s (extension %s)", path, ext)
                continue

            # Check 3: Retrieve content at head commit SHA
            try:
                content = effective_client.get_file_content(
                    owner, repo_name, path, ref=sha_clean
                )
                size_bytes = len(content.encode("utf-8"))
                if size_bytes > self.max_file_size_bytes:
                    batch.results[path] = FileContentResult(
                        path=path,
                        content=None,
                        status=FileContentStatus.OVERSIZED,
                        error=f"File exceeds maximum allowed size ({size_bytes} > {self.max_file_size_bytes} bytes)",
                        size_bytes=size_bytes,
                    )
                    logger.warning(
                        "File %s exceeds size limit (%d bytes, max %d)",
                        path,
                        size_bytes,
                        self.max_file_size_bytes,
                    )
                else:
                    batch.results[path] = FileContentResult(
                        path=path,
                        content=content,
                        status=FileContentStatus.SUCCESS,
                        size_bytes=size_bytes,
                    )
                    logger.info(
                        "Retrieved content for %s at %s (%d bytes)",
                        path,
                        sha_clean[:7],
                        size_bytes,
                    )
            except GitHubNotFoundError as exc:
                logger.warning(
                    "File %s not found in %s/%s at ref %s",
                    path,
                    owner,
                    repo_name,
                    sha_clean[:7],
                )
                batch.results[path] = FileContentResult(
                    path=path,
                    content=None,
                    status=FileContentStatus.NOT_FOUND,
                    error=str(exc),
                )
            except GitHubClientError as exc:
                logger.error(
                    "GitHub API error fetching content for %s at ref %s: %s",
                    path,
                    sha_clean[:7],
                    type(exc).__name__,
                )
                batch.results[path] = FileContentResult(
                    path=path,
                    content=None,
                    status=FileContentStatus.ERROR,
                    error=str(exc),
                )
            except Exception as exc:
                logger.error(
                    "Unexpected error fetching content for %s: %s",
                    path,
                    type(exc).__name__,
                )
                batch.results[path] = FileContentResult(
                    path=path,
                    content=None,
                    status=FileContentStatus.ERROR,
                    error=str(exc),
                )

        return batch

    @staticmethod
    def _extract_path_and_status(item: Any) -> tuple[str, str]:
        if isinstance(item, ChangedFile):
            return item.path.strip(), item.status.strip()
        if isinstance(item, dict):
            raw_path = item.get("path") or item.get("filename") or ""
            raw_status = item.get("status") or "modified"
            return str(raw_path).strip(), str(raw_status).strip()
        if isinstance(item, str):
            return item.strip(), "modified"
        return "", ""


ChangedFileContentService = GitHubFileContentService
