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

    def status_porcelain(self) -> str:
        return self._run(["status", "--porcelain"])

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
