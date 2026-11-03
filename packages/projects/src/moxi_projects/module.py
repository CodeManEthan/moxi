"""The announcement of Moxi's projects module, which moxi.modules reads.

It offers interface 1, the read API in `moxi_projects.api`, the verb `moxi projects`, and,
with the `ui` extra, a page in the browser UI shell and three components other pages may
place: a project picker, a project's card and the add form.
"""

INTERFACES = frozenset({1})
API = "moxi_projects.api"
#: What `moxi projects` runs, in process, as `main(argv, prog="moxi projects")`.
CLI = "moxi_projects.cli:main"
SUMMARY = "Moxi's list of named places: projects, their roots, kinds and agents."
OPTIONAL = {
    "ui": "The projects page in the browser UI shell: the list, add (with create), edit, "
          "the agents and remove.",
}
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
#: projects' page server, which the browser UI shell starts. Nothing here imports it.
PAGE = "moxi_projects.page:PAGE"
#: The page, and three parts any page may place: the picker, whose choice sends its host
#: a `moxi-change` with the key only (never the root), a project's card, and the add form.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Projects", "contract": 1, "place": "page",
             "doc": "Your projects: each one's key, root, kind and agents, and add"},
    "picker": {"script": "picker.js", "title": "Project picker", "contract": 1,
               "place": "inline",
               "doc": "Choose a project, by key with its root shown; sends a bubbling, "
                      "composed moxi-change whose detail is {key}",
               "attributes": {"kind": {"type": "string",
                                       "doc": "list only the projects of this kind"}}},
    "card": {"script": "card.js", "title": "Project", "contract": 1, "place": "inline",
             "doc": "One project's key, kind, root, lead and associated agents",
             "attributes": {"key": {"type": "string", "required": True,
                                    "doc": "the project's key, matched ignoring case"}}},
    "add": {"script": "add.js", "title": "Add a project", "contract": 1, "place": "block",
            "doc": "The add form, with create, as moxi projects add takes it",
            "attributes": {"kind": {"type": "string",
                                    "doc": "the kind the form starts with"}}},
}
#: projects' own checks of its store and rows.
DOCTOR = "moxi_projects.doctor:rows"
