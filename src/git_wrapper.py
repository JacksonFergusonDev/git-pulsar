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

    def merge_squash(self, branch: str) -> None:
        self._run(["merge", "--squash", branch], capture=False)

    def branch_reset(self, branch: str, target: str) -> None:
        self._run(["branch", "-f", branch, target], capture=False)

    def get_last_commit_time(self, branch: str) -> str:
        try:
            return self._run(["log", "-1", "--format=%cr", branch])
        except Exception:
            return "Never"

    def get_untracked_files(self) -> list[str]:
        output = self._run(["ls-files", "--others", "--exclude-standard"])
        return output.splitlines() if output else []

    def run_diff(self, target: str) -> None:
        """Runs git diff attached to stdout (no capture)."""
        self._run(["diff", target], capture=False)
