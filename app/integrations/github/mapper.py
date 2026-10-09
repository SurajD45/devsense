"""DevSense — GitHub PR context to domain model mapper."""

from __future__ import annotations

from app.domain.models import ChangedFile, Commit, PullRequest


class GitHubPullRequestMapper:
    """Transform fetched GitHub PR context into a validated domain model.

    This mapper is deterministic and performs no network, database,
    authentication, or credential-handling operations.
    """

    def to_pull_request(
        self,
        context: dict,
        owner: str,
        repo: str,
    ) -> PullRequest:
        """Map aggregated GitHub context into a canonical PullRequest.

        Args:
            context: Result from GitHubClient.get_pull_request_context().
            owner: GitHub repository owner.
            repo: GitHub repository name.

        Returns:
            A validated PullRequest containing commits and changed files.

        Raises:
            ValueError: If the context structure or repository identity is invalid.
            pydantic.ValidationError: If mapped data violates domain model rules.
        """
        if not isinstance(context, dict):
            raise ValueError("context must be a dictionary")

        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be a non-empty string")

        if not isinstance(repo, str) or not repo.strip():
            raise ValueError("repo must be a non-empty string")

        pr_data = context.get("pull_request")
        files_data = context.get("files")
        commits_data = context.get("commits")

        if not isinstance(pr_data, dict):
            raise ValueError("context must contain a pull_request dictionary")

        if not isinstance(files_data, list):
            raise ValueError("context must contain a files list")

        if not isinstance(commits_data, list):
            raise ValueError("context must contain a commits list")

        if not all(isinstance(item, dict) for item in files_data):
            raise ValueError("each changed file must be a dictionary")

        if not all(isinstance(item, dict) for item in commits_data):
            raise ValueError("each commit must be a dictionary")

        changed_files = [
            ChangedFile(
                path=item.get("filename"),
                status=item.get("status"),
                additions=item.get("additions", 0),
                deletions=item.get("deletions", 0),
                patch=item.get("patch"),
            )
            for item in files_data
        ]

        commits = [
            Commit(
                sha=item.get("sha"),
                message=item.get("message"),
                author=item.get("author"),
            )
            for item in commits_data
        ]

        return PullRequest(
            repository=f"{owner.strip()}/{repo.strip()}",
            number=pr_data.get("number"),
            title=pr_data.get("title"),
            description=pr_data.get("body"),
            author=pr_data.get("author"),
            source_branch=pr_data.get("head_branch"),
            target_branch=pr_data.get("base_branch"),
            state=pr_data.get("state"),
            commits=commits,
            changed_files=changed_files,
        )
