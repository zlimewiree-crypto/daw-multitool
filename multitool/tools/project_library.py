"""Project Library adapter for the shared DAW Multitool home screen."""


def open_tool(window):
    from multitool.library.ui import LibraryApp
    return LibraryApp(window)


def self_check():
    from multitool.library.core import load_settings, visible, Library
    from multitool.library.ui import LibraryApp
    if visible(Library({}, []), "All projects") != []:
        raise RuntimeError("Project Library returned rows for an empty library")
    load_settings()
