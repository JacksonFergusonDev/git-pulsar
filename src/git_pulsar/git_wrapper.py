import logging
import re
import subprocess
from pathlib import Path

from .constants import APP_NAME

logger = logging.getLogger(APP_NAME)


class GitRepo:
    """A wrapper around the Git command-line interface for a specific repository.

    This class provides methods to execute common Git operations using `subprocess`,
    abstracting away the command construction and output handling. It is designed
    to work with both standard working directories and temporary index environments.

    Attributes:
        path (Path): The file system path to the repository root.
    """

    def __init__(self, path: Path):
        """Initializes the GitRepo instance.

        Args:
            path (Path): The path to the repository root directory.

        Raises:
            ValueError: If the specified path does not contain a .git directory.
        """
        self.path = path
        if not (self.path / ".git").exists():
            raise ValueError(f"Not a git repository: {self.path}")

    def _run(
        self, args: list[str], capture: bool = True, env: dict | None = None
    ) -> str:
        """Executes a Git command within the repository context.

        Args:
            args (list[str]): A list of arguments to pass to the git command.
            capture (bool, optional):   Whether to capture and return stdout.
                                        Defaults to True.
            env (Optional[dict], optional): Environment variables to pass to the
                                            subprocess. Useful for manipulating
                                            GIT_INDEX_FILE. Defaults to None.

        Returns:
            str:    The stripped stdout of the command if capture is True,
                    otherwise an empty string.

        Raises:
            RuntimeError: If the git command returns a non-zero exit code.
        """
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
        """Retrieves the name of the currently checked-out branch.

        Returns:
            str: The name of the current branch.
        """
        return self._run(["branch", "--show-current"])

    def status_porcelain(self, path: str | None = None) -> list[str]:
        """Returns the porcelain (machine-readable) status of the repository.

        Args:
            path (Optional[str], optional): A specific path to check status for.
                                            Defaults to None.

        Returns:
            list[str]: A list of status lines returned by `git status --porcelain`.
        """
        cmd = ["status", "--porcelain"]
        if path:
            cmd.append(path)
        output = self._run(cmd)
        return output.splitlines() if output else []

    def commit_interactive(self) -> None:
        """Triggers a standard git commit, opening the configured text editor.

        This method captures no output, allowing the editor to take over the terminal.
        """
        self._run(["commit"], capture=False)

    def checkout(
        self, branch: str, file: str | None = None, force: bool = False
    ) -> None:
        """Checks out a specific branch or restores a file.

        Args:
            branch (str): The target branch name or commit hash.
            file (Optional[str], optional): A specific file path to checkout.
                                            Defaults to None.
            force (bool, optional): Whether to force the checkout (discarding changes).
                                    Defaults to False.
        """
        cmd = ["checkout"]
        if force:
            cmd.append("-f")
        cmd.append(branch)
        if file:
            cmd.extend(["--", file])
        self._run(cmd, capture=False)

    def commit(self, message: str, no_verify: bool = False) -> None:
        """Creates a new commit with the provided message.

        Args:
            message (str): The commit message.
            no_verify (bool, optional): Whether to bypass pre-commit hooks
                                        (`--no-verify`). Defaults to False.
        """
        cmd = ["commit", "-m", message]
        if no_verify:
            cmd.append("--no-verify")
        self._run(cmd, capture=False)

    def add_all(self) -> None:
        """
        Stages all changes (modified, deleted, and untracked files)
        in the working directory.
        """
        self._run(["add", "."], capture=False)

    def merge_squash(self, *branches: str) -> None:
        """Performs a squash merge of the specified branches into the current HEAD.

        This stages the changes but does not commit them.

        Args:
            *branches (str): Variable length argument list of branch names to merge.
        """
        if not branches:
            return
        self._run(["merge", "--squash", *branches], capture=False)

    def branch_reset(self, branch: str, target: str) -> None:
        """Forcefully resets a branch pointer to a specific target commit.

        Args:
            branch (str): The branch name to reset.
            target (str): The target commit SHA or reference.
        """
        self._run(["branch", "-f", branch, target], capture=False)

    def list_refs(self, pattern: str) -> list[str]:
        """Lists references matching a specific pattern.

        Args:
            pattern (str): The glob pattern to match (e.g., 'refs/heads/wip/*').

        Returns:
            list[str]: A list of matching reference names.
        """
        try:
            output = self._run(["for-each-ref", "--format=%(refname)", pattern])
            return output.splitlines() if output else []
        except Exception as e:
            logger.warning(f"Git error listing refs for {pattern}: {e}")
            return []

    def get_last_commit_time(self, branch: str) -> str:
        """Gets the relative time since the last commit on a specified branch.

        Args:
            branch (str): The branch to check.

        Returns:
            str: A human-readable relative time string (e.g., '2 hours ago').

        Raises:
            RuntimeError: If the branch does not exist or the command fails.
        """
        return self._run(["log", "-1", "--format=%cr", branch])

    def rev_parse(self, rev: str) -> str | None:
        """Resolves a revision (tag, branch, relative ref) to a full SHA-1 hash.

        Args:
            rev (str): The revision to parse (e.g., 'HEAD', 'master').

        Returns:
            Optional[str]:  The full SHA-1 hash,
                            or None if the revision could not be resolved.
        """
        try:
            return self._run(["rev-parse", rev])
        except Exception as e:
            logger.debug(f"rev-parse failed for '{rev}': {e}")
            return None

    def write_tree(self, env: dict | None = None) -> str:
        """Creates a tree object from the current index.

        Args:
            env (Optional[dict], optional): Environment variables,
                                            used to specify a temporary index.

        Returns:
            str: The SHA-1 hash of the created tree object.
        """
        return self._run(["write-tree"], env=env)

    def commit_tree(
        self, tree: str, parents: list[str], message: str, env: dict | None = None
    ) -> str:
        """Creates a commit object from a tree object.

        Args:
            tree (str): The tree SHA-1 to commit.
            parents (list[str]): A list of parent commit SHA-1s.
            message (str): The commit message.
            env (Optional[dict], optional): Environment variables to
                                            pass to the subprocess.

        Returns:
            str: The SHA-1 hash of the new commit.
        """
        cmd = ["commit-tree", tree, "-m", message]
        for p in parents:
            cmd.extend(["-p", p])
        try:
            return self._run(cmd, env=env)
        except Exception as e:
            logger.warning(f"Failed to commit tree {tree}: {e}")
            raise

    def update_ref(self, ref: str, new_oid: str, old_oid: str | None = None) -> None:
        """Safely updates a reference to a new object ID.

        Args:
            ref (str): The reference to update (e.g., 'refs/heads/master').
            new_oid (str): The new SHA-1 hash.
            old_oid (Optional[str], optional): The expected old SHA-1 hash. If provided,
                                               the update will fail if the current ref
                                               does not match this value.
        """
        cmd = ["update-ref", "-m", "Pulsar backup", ref, new_oid]
        if old_oid:
            cmd.append(old_oid)
        try:
            self._run(cmd)
        except Exception as e:
            logger.warning(f"Failed to update ref {ref}: {e}")
            raise

    def get_untracked_files(self) -> list[str]:
        """Lists files that are not tracked by git and are not ignored.

        Returns:
            list[str]: A list of untracked file paths.
        """
        output = self._run(["ls-files", "--others", "--exclude-standard"])
        return output.splitlines() if output else []

    def run_diff(self, target: str, file: str | None = None) -> None:
        """Executes a git diff operation, outputting directly to stdout.

        Args:
            target (str): The target revision or branch to diff against.
            file (str | None, optional): A specific file path to diff. Defaults to None.
        """
        cmd = ["diff", target]
        if file:
            cmd.extend(["--", file])
        self._run(cmd, capture=False)

    def diff_shortstat(self, target: str, source: str) -> tuple[int, int, int]:
        """Retrieves the shortstat differences between two references.

        Executes `git diff --shortstat target...source` to determine the
        number of files changed, insertions, and deletions present in the
        source reference that are not in the target.

        Args:
            target (str): The base reference (e.g., 'main').
            source (str): The branch or commit to compare (e.g., a backup ref).

        Returns:
            tuple[int, int, int]: A tuple containing (files_changed, insertions, deletions).
                                  Returns (0, 0, 0) if there are no differences or parsing fails.
        """
        try:
            output = self._run(["diff", "--shortstat", f"{target}...{source}"])
            if not output:
                return 0, 0, 0

            files_match = re.search(r"(\d+)\s+file", output)
            insertions_match = re.search(r"(\d+)\s+insertion", output)
            deletions_match = re.search(r"(\d+)\s+deletion", output)

            files = int(files_match.group(1)) if files_match else 0
            insertions = int(insertions_match.group(1)) if insertions_match else 0
            deletions = int(deletions_match.group(1)) if deletions_match else 0

            return files, insertions, deletions
        except Exception as e:
            logger.warning(f"Failed to parse shortstat for {target}...{source}: {e}")
            return 0, 0, 0
