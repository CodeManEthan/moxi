"""The announcement of fixture, the module projects' page tests install.

Its `project-facts` component reports to the Projects page what a test told its page
server to say, as any module's facts component would report its own module's facts. Its
page is where a section's link goes, and its link with projects refuses to remove a
project while a test says so.
"""

INTERFACES = frozenset({1})
SUMMARY = "A module projects' page tests install"
PAGE = "moxi_fixture.page:PAGE"
COMPONENTS = {
    "page": {"script": "page.js", "title": "Fixture", "doc": "The fixture's page, its route shown",
             "contract": 1, "place": "page"},
    "project-facts": {"script": "project-facts.js", "title": "Fixture facts",
                      "doc": "What a test told the fixture to say of each project",
                      "contract": 1, "place": "inline",
                      "attributes": {"projects": {"type": "string",
                                                  "doc": "the projects the page holds, JSON"},
                                     "open": {"type": "string",
                                              "doc": "the key of the opened project"}}},
}
