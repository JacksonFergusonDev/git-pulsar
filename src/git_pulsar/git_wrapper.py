import logging
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .constants import APP_NAME
from .types import (
    BranchName,
    CommitSHA,
    CommitTreeParams,
    DiffStat,
    GitOID,
    GitRef,
    TreeSHA,
)

logger = logging.getLogger(APP_NAME)


def get_git_dir(repo_path: Path) -> Path:
    """Resolves the git directory (.git or worktree gitdir) for a repository path."""
    dot_git = repo_path / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            return GitRepo(repo_path).git_dir
        except Exception:
            pass
    return dot_git


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
        self, args: list[str], capture: bool = True, env: dict[str, str] | None = None
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
        logger.debug(f"Executing: git {' '.join(args)}")

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

    @property
    def git_dir(self) -> Path:
        """Resolves the absolute path to the git directory (.git or worktree gitdir)."""
        dot_git = self.path / ".git"
        if dot_git.is_dir():
            return dot_git
        try:
            res = self._run(["rev-parse", "--git-dir"])
            p = Path(res)
            return p if p.is_absolute() else (self.path / p).resolve()
        except Exception:
            return dot_git

    def current_branch(self) -> BranchName:
        """Retrieves the name of the currently checked-out branch.

        Returns:
            BranchName: The name of the current branch.
        """
        return BranchName(self._run(["branch", "--show-current"]))

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
            cmd.extend(["--", path])
        output = self._run(cmd)
        return output.splitlines() if output else []

    def commit_interactive(self) -> None:
        """Triggers a standard git commit, opening the configured text editor.

        This method captures no output, allowing the editor to take over the terminal.
        """
        self._run(["commit"], capture=False)

    def checkout(
        self,
        branch: BranchName | GitRef | str,
        file: str | None = None,
        force: bool = False,
    ) -> None:
        """Checks out a specific branch or restores a file.

        Args:
            branch (BranchName | GitRef | str): The target branch name or commit hash.
            file (Optional[str], optional): A specific file path to checkout.
                                            Defaults to None.
            force (bool, optional): Whether to force the checkout (discarding changes).
                                    Defaults to False.
        """
        cmd = ["checkout"]
        if force:
            cmd.append("-f")
        cmd.append(str(branch))
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
        """Stages all changes (modified, deleted, and untracked files) in the working directory."""
        self._run(["add", "."], capture=False)

    def merge_squash(self, *branches: BranchName | GitRef | str) -> None:
        """Performs a squash merge of the specified branches into the current HEAD.

        This stages the changes but does not commit them.

        Args:
            *branches (BranchName | GitRef | str): Variable length argument list of branch names to merge.
        """
        if not branches:
            return
        self._run(["merge", "--squash", *[str(b) for b in branches]], capture=False)

    def branch_reset(
        self,
        branch: BranchName | GitRef | str,
        target: GitOID | CommitSHA | str,
    ) -> None:
        """Forcefully resets a branch pointer to a specific target commit.

        Args:
            branch (BranchName | GitRef | str): The branch name to reset.
            target (GitOID | CommitSHA | str): The target commit SHA or reference.
        """
        self._run(["branch", "-f", str(branch), str(target)], capture=False)

    def list_refs(self, pattern: str) -> list[GitRef]:
        """Lists references matching a specific pattern.

        Args:
            pattern (str): The glob pattern to match (e.g., 'refs/heads/wip/*').

        Returns:
            list[GitRef]: A list of matching reference names.
        """
        try:
            output = self._run(["for-each-ref", "--format=%(refname)", pattern])
            return [GitRef(line) for line in output.splitlines()] if output else []
        except Exception as e:
            logger.warning(f"Git error listing refs for {pattern}: {e}")
            return []

    def get_last_commit_time(self, branch: BranchName | GitRef | str) -> str:
        """Gets the relative time since the last commit on a specified branch.

        Args:
            branch (BranchName | GitRef | str): The branch to check.

        Returns:
            str: A human-readable relative time string (e.g., '2 hours ago').

        Raises:
            RuntimeError: If the branch does not exist or the command fails.
        """
        return self._run(["log", "-1", "--format=%cr", str(branch)])

    def get_commit_timestamp(self, ref: GitRef | BranchName | str) -> int | None:
        """Retrieves the Unix timestamp of the latest commit on a given ref.

        Args:
            ref (GitRef | BranchName | str): The git reference or revision.

        Returns:
            int | None: Unix timestamp of the commit, or None if the ref does not exist or fails.
        """
        try:
            output = self._run(["log", "-1", "--format=%ct", str(ref)])
            return int(output.strip())
        except Exception as e:
            logger.debug(f"Could not get commit timestamp for '{ref}': {e}")
            return None

    def rev_parse(self, rev: GitRef | BranchName | str) -> GitOID | None:
        """Resolves a revision (tag, branch, relative ref) to a full SHA-1 hash.

        Args:
            rev (GitRef | BranchName | str): The revision to parse (e.g., 'HEAD', 'master').

        Returns:
            Optional[GitOID]: The full SHA-1 hash,
                              or None if the revision could not be resolved.
        """
        try:
            res = self._run(["rev-parse", str(rev)])
            return GitOID(res) if res else None
        except Exception as e:
            logger.debug(f"rev-parse failed for '{rev}': {e}")
            return None

    def write_tree(self, env: dict[str, str] | None = None) -> TreeSHA:
        """Creates a tree object from the current index.

        Args:
            env (Optional[dict], optional): Environment variables,
                                            used to specify a temporary index.

        Returns:
            TreeSHA: The SHA-1 hash of the created tree object.
        """
        return TreeSHA(GitOID(self._run(["write-tree"], env=env)))

    def commit_tree(
        self,
        tree: TreeSHA | GitOID | str | CommitTreeParams,
        parents: Sequence[GitOID | CommitSHA | str] | None = None,
        message: str = "",
        env: dict[str, str] | None = None,
    ) -> CommitSHA:
        """Creates a commit object from a tree object.

        Accepts either a `CommitTreeParams` parameter object or individual parameters.

        Args:
            tree (TreeSHA | GitOID | str | CommitTreeParams): The tree SHA-1 or a CommitTreeParams object.
            parents (Sequence[GitOID | CommitSHA | str] | None, optional): A sequence of parent commit SHA-1s.
            message (str, optional): The commit message.
            env (Optional[dict], optional): Environment variables to pass to the subprocess.

        Returns:
            CommitSHA: The SHA-1 hash of the new commit.
        """
        if isinstance(tree, CommitTreeParams):
            actual_tree = tree.tree
            actual_parents = tree.parents
            actual_message = tree.message
            actual_env = tree.env
        else:
            actual_tree = tree
            actual_parents = parents or []
            actual_message = message
            actual_env = env

        cmd = ["commit-tree", str(actual_tree), "-m", actual_message]
        for p in actual_parents:
            cmd.extend(["-p", str(p)])
        try:
            return CommitSHA(GitOID(self._run(cmd, env=actual_env)))
        except Exception as e:
            logger.warning(f"Failed to commit tree {actual_tree}: {e}")
            raise

    def update_ref(
        self,
        ref: GitRef | str,
        new_oid: GitOID | CommitSHA | str,
        old_oid: GitOID | CommitSHA | str | None = None,
    ) -> None:
        """Safely updates a reference to a new object ID.

        Args:
            ref (GitRef | str): The reference to update (e.g., 'refs/heads/master').
            new_oid (GitOID | CommitSHA | str): The new SHA-1 hash.
            old_oid (Optional[GitOID | CommitSHA | str], optional): The expected old SHA-1 hash. If provided,
                                                                    the update will fail if the current ref
                                                                    does not match this value.
        """
        cmd = ["update-ref", "-m", "Pulsar backup", str(ref), str(new_oid)]
        if old_oid:
            cmd.append(str(old_oid))
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

    def run_diff(
        self, target: GitRef | BranchName | str, file: str | None = None
    ) -> None:
        """Executes a git diff operation, outputting directly to stdout.

        Args:
            target (GitRef | BranchName | str): The target revision or branch to diff against.
            file (str | None, optional): A specific file path to diff. Defaults to None.
        """
        cmd = ["diff", str(target)]
        if file:
            cmd.extend(["--", file])
        self._run(cmd, capture=False)

    def diff_shortstat(
        self,
        target: GitRef | BranchName | str,
        source: GitRef | BranchName | str,
    ) -> DiffStat:
        """Retrieves the shortstat differences between two references.

        Executes `git diff --shortstat target...source` to determine the
        number of files changed, insertions, and deletions present in the
        source reference that are not in the target.

        Args:
            target (GitRef | BranchName | str): The base reference (e.g., 'main').
            source (GitRef | BranchName | str): The branch or commit to compare (e.g., a backup ref).

        Returns:
            DiffStat: A DiffStat NamedTuple containing (files_changed, insertions, deletions).
                      Returns DiffStat(0, 0, 0) if there are no differences or parsing fails.
        """
        try:
            output = self._run(["diff", "--shortstat", f"{target}...{source}"])
            if not output:
                return DiffStat(0, 0, 0)

            files_match = re.search(r"(\d+)\s+file", output)
            insertions_match = re.search(r"(\d+)\s+insertion", output)
            deletions_match = re.search(r"(\d+)\s+deletion", output)

            files = int(files_match.group(1)) if files_match else 0
            insertions = int(insertions_match.group(1)) if insertions_match else 0
            deletions = int(deletions_match.group(1)) if deletions_match else 0

            return DiffStat(files, insertions, deletions)
        except Exception as e:
            logger.warning(f"Failed to parse shortstat for {target}...{source}: {e}")
            return DiffStat(0, 0, 0)
