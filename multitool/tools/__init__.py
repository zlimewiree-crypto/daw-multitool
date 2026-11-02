"""Register each tool once; the home screen and app builder use this same list."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    description: str
    module: str  # exports open_tool(window), returning a controller with close()


TOOLS = (
    Tool("organizer", "Audio Organizer",
         "Sort beats and melodies, listen to the results, and teach the sorter with your labels.",
         "multitool.tools.organizer"),
    Tool("auto_poster", "Auto Poster",
         "Turn audio and an image or video into a post, then publish to your connected social accounts.",
         "multitool.tools.auto_poster"),
    Tool("project_library", "Project Library",
         "Find any FL Studio, Ableton or Pro Tools project, see the beats exported from it, and open it in its DAW.",
         "multitool.tools.project_library"),
    Tool("varispeed", "Varispeed",
         "Speed a beat up or slow it down like tape, hear it live, and save copies with the new BPM and key.",
         "multitool.tools.varispeed"),
)
