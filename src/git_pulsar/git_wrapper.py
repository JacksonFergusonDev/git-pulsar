import subprocess
from pathlib import Path
from typing import Optional


class GitRepo:
    def __init__(self, path: Path):
        self.path = path
        if not (self.path / ".git").exists():
            raise ValueError(f"Not a git repository: {self.path}")

    def _run(
        self, args: list[str], capture: bool = True, env: Optional[dict] = None
    ) -> str:
        try:
            res = subprocess.run(
                ["git", *args],
                cwd=self.path,
                capture_output=capture,
                text=True,
                check=True,
                env=env,
            )
            return res.stdout.strip() if capture else ""
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git error: {e.stderr or e}") from e

    def current_branch(self) -> str:
        return self._run(["branch", "--show-current"])

    def status_porcelain(self, path: Optional[str] = None) -> list[str]:
        cmd = ["status", "--porcelain"]
        if path:
            cmd.append(path)
        output = self._run(cmd)
        return output.splitlines() if output else []

    def commit_interactive(self) -> None:
        """Opens the editor for a commit message."""
        self._run(["commit"], capture=False)

    def checkout(
        self, branch: str, file: Optional[str] = None, force: bool = False
    ) -> None:
        cmd = ["checkout"]
        if force:
            cmd.append("-f")
        cmd.append(branch)
        if file:
            cmd.extend(["--", file])
        self._run(cmd, capture=False)

    def commit(self, message: str, no_verify: bool = False) -> None:
        cmd = ["commit", "-m", message]
        if no_verify:
            cmd.append("--no-verify")
        self._run(cmd, capture=False)

    def add_all(self) -> None:
        self._run(["add", "."], capture=False)

    def merge_squash(self, *branches: str) -> None:
        if not branches:
            return
        self._run(["merge", "--squash", *branches], capture=False)

    def branch_reset(self, branch: str, target: str) -> None:
        self._run(["branch", "-f", branch, target], capture=False)

    def list_refs(self, pattern: str) -> list[str]:
        """Returns a list of refs matching the pattern (e.g. 'refs/heads/wip/*')."""
        try:
            output = self._run(["for-each-ref", "--format=%(refname)", pattern])
            return output.splitlines() if output else []
        except Exception:
            return []

    def get_last_commit_time(self, branch: str) -> str:
        """
        Returns relative time string (e.g. '2 hours ago').
        Raises RuntimeError if branch doesn't exist or git fails.
        """
        return self._run(["log", "-1", "--format=%cr", branch])

    def rev_parse(self, rev: str) -> Optional[str]:
        """Resolves a revision to a full SHA-1."""
        try:
            return self._run(["rev-parse", rev])
        except Exception:
            return None

    def write_tree(self, env: Optional[dict] = None) -> str:
        """Writes the current index to a tree object."""
        return self._run(["write-tree"], env=env)

    def commit_tree(
        self, tree: str, parents: list[str], message: str, env: Optional[dict] = None
    ) -> str:
        """Creates a commit object from a tree."""
        cmd = ["commit-tree", tree, "-m", message]
        for p in parents:
            cmd.extend(["-p", p])
        return self._run(cmd, env=env)

    def update_ref(self, ref: str, new_oid: str, old_oid: Optional[str] = None) -> None:
        """Safely updates a ref."""
        cmd = ["update-ref", "-m", "Pulsar backup", ref, new_oid]
        if old_oid:
            cmd.append(old_oid)
        self._run(cmd)

    def get_untracked_files(self) -> list[str]:
        output = self._run(["ls-files", "--others", "--exclude-standard"])
        return output.splitlines() if output else []

    def run_diff(self, target: str) -> None:
        """Runs git diff attached to stdout (no capture)."""
        self._run(["diff", target], capture=False)
