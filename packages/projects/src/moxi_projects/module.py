"""The announcement of Moxi's projects module, which moxi.modules reads.

It offers interface 1, the read API in `moxi_projects.api`, and the verb `moxi projects`.
"""

INTERFACES = frozenset({1})
API = "moxi_projects.api"
#: What `moxi projects` runs, in process, as `main(argv, prog="moxi projects")`.
CLI = "moxi_projects.cli:main"
SUMMARY = "Moxi's list of named places: projects, their roots, kinds and agents."
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = None
#: Run again by `moxi install projects`, the upgrade path: it migrates an existing store
#: and makes none.
SETUP = "moxi_projects.store:setup"
SEGMENT = None
ANNOUNCES = 1
#: The one store. The first write makes it, so the core's doctor reads it as fine while
#: it is missing. It holds roots typed by hand, so nothing can rebuild it.
STORES = {"projects": {"root": "data", "path": "projects.db", "kind": "sqlite",
                       "private": True, "retention": "keep", "rebuildable": False,
                       "lazy": True}}
#: projects' own checks of its store and rows.
DOCTOR = "moxi_projects.doctor:rows"
