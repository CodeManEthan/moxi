"""The branch, read from the repository's `HEAD` file: no process."""

from moxi_statusline.fields import Options, branch, field

HASH = "0123456789abcdef0123456789abcdef01234567"


def repo(repo_dir, head: str):
    (repo_dir / ".git").mkdir(parents=True)
    (repo_dir / ".git" / "HEAD").write_text(head)
    return repo_dir


def test_a_branch(tmp_path):
    repo(tmp_path / "r", "ref: refs/heads/feature/one\n")
    assert branch(str(tmp_path / "r")) == "feature/one"


def test_found_from_a_dir_inside_the_repository(tmp_path):
    repo(tmp_path / "r", "ref: refs/heads/main\n")
    inner = tmp_path / "r" / "a" / "b"
    inner.mkdir(parents=True)
    assert branch(str(inner)) == "main"


def test_a_detached_head_shows_seven_characters(tmp_path):
    repo(tmp_path / "r", HASH + "\n")
    assert branch(str(tmp_path / "r")) == "0123456"


def test_a_worktree_file_with_an_absolute_gitdir(tmp_path):
    git_dir = tmp_path / "main" / ".git" / "worktrees" / "wt"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/wt-branch\n")
    tree = tmp_path / "wt"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {git_dir}\n")
    assert branch(str(tree)) == "wt-branch"


def test_a_worktree_file_with_a_relative_gitdir(tmp_path):
    git_dir = tmp_path / "super" / ".git" / "modules" / "sub"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text(HASH)
    sub = tmp_path / "super" / "sub"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: ../.git/modules/sub\n")
    assert branch(str(sub / ".")) == "0123456"


def test_a_reftable_head_shows_nothing(tmp_path):
    repo(tmp_path / "r", "ref: refs/heads/.invalid\n")
    assert branch(str(tmp_path / "r")) is None


def test_no_repository_shows_nothing(tmp_path):
    assert branch(str(tmp_path)) is None


def test_a_git_file_without_gitdir_shows_nothing(tmp_path):
    (tmp_path / ".git").write_text("something else\n")
    assert branch(str(tmp_path)) is None


def test_the_branch_field_reads_the_input_dir(tmp_path):
    repo(tmp_path / "r", "ref: refs/heads/main\n")
    payload = {"workspace": {"current_dir": str(tmp_path / "r")}}
    assert field("branch", payload, Options()) == "main"
    assert field("branch", {}, Options()) is None
