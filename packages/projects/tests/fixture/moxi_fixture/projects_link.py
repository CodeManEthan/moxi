"""The fixture's link with projects: while a file named `hold` is in the fixture's state
directory, it refuses to remove any project."""

from moxi.paths import Layout

INTERFACE = 1


def refuse_remove(project):
    if (Layout("fixture").state_dir() / "hold").exists():
        return f"the fixture holds {project.key}; let it go on the fixture's page"
    return None
