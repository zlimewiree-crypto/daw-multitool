"""Shared studio styling for every window: purple on black, the Mac's system font and one type scale, so no
window sets its own fonts or colours. Compact, rounded, anti-aliased controls; hairline borders; one accent colour
used sparingly, so the one main action on each screen stands out."""
import math
import tkinter as tk
from tkinter import font, ttk

BACKGROUND = "#09080d"
HEADER = "#0e0d14"        # the strip along the top of each window
PANEL = "#111018"         # cards, tables, the sidebar and the status bar
RAISED = "#1a1822"        # buttons, hovered rows
RAISED_HOVER = "#232030"
FIELD = "#0d0c12"         # text boxes
BORDER = "#221f2b"        # hairlines between areas
EDGE = "#2f2b3b"          # the rim of buttons and fields
TEXT = "#f2eff8"
MUTED = "#9893a8"
ACCENT = "#7c3aed"        # purple: main buttons, ticks, selections (white text on it: 5.7:1 contrast)
ACCENT_HOVER = "#8b5cf6"
ACCENT_PRESSED = "#6d28d9"
ACCENT_TEXT = "#c4b0ff"   # purple that reads as text on black
ACCENT_SOFT = "#1c1530"   # purple-tinted tiles behind icons
ACCENT_DARK = "#ffffff"   # text on purple
DANGER = "#ff7b88"
SELECTED = "#2a1e4f"
DISABLED = "#6b6580"
PLATFORM_COLOURS = {"YouTube": "#ff4e45", "TikTok": "#25f4ee", "Instagram": "#e1306c"}

SIZES = {"Title": (20, "bold"), "Heading": (15, "bold"), "Step": (14, "normal"), "Strong": (13, "bold"),
         "Field": (13, "bold"), "Small": (13, "normal"), "Eyebrow": (11, "bold")}  # the Mac's own 13 pt body size


def apply_theme(root):
    owner = root._root()
    if getattr(owner, "studio_theme", False):
        return
    owner.studio_theme = True
    style = ttk.Style(root)
    style.theme_use("clam")
    base = font.nametofont("TkDefaultFont", root=root)
    base.configure(size=13)  # keeps the Mac's own system font at its standard size
    family = base.actual("family")
    for name in ("TkTextFont", "TkMenuFont", "TkHeadingFont"):
        font.nametofont(name, root=root).configure(family=family, size=13)
    fonts = {name: (family, size, weight) for name, (size, weight) in SIZES.items()}

    for pattern, value in (("*background", BACKGROUND), ("*foreground", TEXT), ("*Text.background", FIELD),
                           ("*Text.foreground", TEXT), ("*Text.insertBackground", TEXT), ("*Text.selectBackground", SELECTED),
                           ("*Text.relief", "flat"), ("*Text.highlightThickness", 1), ("*Text.highlightBackground", EDGE),
                           ("*Text.highlightColor", ACCENT), ("*Text.padX", 10), ("*Text.padY", 8), ("*Text.font", "TkDefaultFont"),
                           ("*TCombobox*Listbox.background", PANEL), ("*TCombobox*Listbox.foreground", TEXT),
                           ("*TCombobox*Listbox.selectBackground", SELECTED), ("*TCombobox*Listbox.selectForeground", TEXT),
                           ("*Listbox.background", FIELD), ("*Listbox.foreground", TEXT), ("*Canvas.highlightThickness", 0)):
        owner.option_add(pattern, value)
    root.configure(background=BACKGROUND)

    style.configure(".", background=BACKGROUND, foreground=TEXT, font="TkDefaultFont", bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, troughcolor=PANEL, focuscolor=ACCENT, selectbackground=SELECTED,
                    selectforeground=TEXT, insertcolor=TEXT)
    for name, value in fonts.items():
        style.configure(f"{name}.TLabel", font=value)
    style.configure("Muted.TLabel", foreground=MUTED, font=fonts["Small"])
    style.configure("Eyebrow.TLabel", foreground=ACCENT_TEXT)
    style.configure("Empty.TLabel", background=PANEL, foreground=MUTED, font=fonts["Small"])

    # three kinds of button: the one main action (purple), normal (dark with a rim), and quiet text-only ones
    style.configure("TButton", padding=(14, 7), background=RAISED, relief="flat", borderwidth=0, focusthickness=1,
                    bordercolor=RAISED, lightcolor=RAISED, darkcolor=RAISED)
    style.map("TButton", foreground=[("disabled", DISABLED)])
    style.configure("Primary.TButton", foreground=ACCENT_DARK, font=fonts["Strong"])
    style.map("Primary.TButton", foreground=[("disabled", DISABLED)])
    style.configure("Big.TButton", padding=(16, 9), font=fonts["Strong"])
    style.configure("Quiet.TButton", foreground=ACCENT_TEXT, padding=(10, 7))  # purple like a link: clearly clickable
    style.map("Quiet.TButton", foreground=[("disabled", DISABLED), ("active", TEXT)])

    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(widget, fieldbackground=FIELD, padding=(10, 7), arrowcolor=MUTED, insertcolor=TEXT,
                        bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD, background=RAISED)
        style.map(widget, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", FIELD)],
                  fieldbackground=[("readonly", FIELD), ("disabled", BACKGROUND)], foreground=[("disabled", DISABLED)])
    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, padding=(0, 3))
        style.map(widget, background=[("active", BACKGROUND), ("disabled", BACKGROUND)], foreground=[("disabled", DISABLED)])

    # sections: a heading and space, no box
    style.configure("TLabelframe", background=BACKGROUND, borderwidth=0, relief="flat", bordercolor=BACKGROUND,
                    lightcolor=BACKGROUND, darkcolor=BACKGROUND, labelmargins=(0, 0, 0, 8))
    for name in ("TLabelframe.Label", "Section.TLabelframe.Label"):
        style.configure(name, font=fonts["Heading"], foreground=TEXT)

    # tabs: plain words, the open one white with a purple underline sitting on a hairline
    style.configure("TNotebook", background=BACKGROUND, borderwidth=0, bordercolor=BACKGROUND, lightcolor=BACKGROUND,
                    darkcolor=BACKGROUND, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", padding=(12, 9), background=BACKGROUND, foreground=MUTED, borderwidth=0,
                    bordercolor=BACKGROUND, lightcolor=BACKGROUND, darkcolor=BACKGROUND, font=fonts["Strong"])
    style.map("TNotebook.Tab", foreground=[("selected", "#ffffff"), ("active", TEXT)], expand=[("selected", (0, 0, 0, 0))],
              padding=[("selected", (12, 9))])  # clam pads the open tab differently, which drops its text

    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, rowheight=32, borderwidth=1,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.map("Treeview", bordercolor=[("user1", ACCENT_TEXT)], lightcolor=[("user1", ACCENT_TEXT)],
              darkcolor=[("user1", ACCENT_TEXT)])
    style.configure("Treeview.Heading", background=PANEL, foreground=MUTED, padding=(10, 7), relief="flat",
                    font=(family, 12, "bold"), bordercolor=BORDER, lightcolor=PANEL, darkcolor=BORDER)
    style.map("Treeview.Heading", background=[("active", RAISED)], foreground=[("active", ACCENT_TEXT)])
    style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", "#ffffff")])
    style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL, thickness=4, borderwidth=0,
                    bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("TSeparator", background=BORDER)

    # cards, the home screen's sidebar and the status bar sit on a lighter panel
    for prefix, colour in (("Card", PANEL), ("Sidebar", PANEL), ("Footer", PANEL), ("Header", HEADER)):
        style.configure(f"{prefix}.TFrame", background=colour)
        style.configure(f"{prefix}Muted.TLabel", background=colour, foreground=MUTED, font=fonts["Small"])
    style.configure("CardHeading.TLabel", background=PANEL, font=fonts["Heading"])
    style.configure("HeaderTitle.TLabel", background=HEADER, foreground="#ffffff", font=fonts["Title"])
    style.configure("HeaderEyebrow.TLabel", background=HEADER, foreground=ACCENT_TEXT, font=fonts["Eyebrow"])
    style.configure("HeaderLink.TLabel", background=HEADER, foreground=MUTED, font=fonts["Small"])
    style.map("HeaderLink.TLabel", foreground=[("active", TEXT)])
    style.configure("SidebarEyebrow.TLabel", background=PANEL, foreground=MUTED, font=fonts["Eyebrow"])
    style.configure("Brand.TLabel", background=PANEL, foreground=TEXT, font=(family, 14, "bold"))
    style.configure("SidebarNav.TLabel", background=PANEL, foreground=MUTED)
    style.map("SidebarNav.TLabel", background=[("active", RAISED)], foreground=[("active", TEXT)])
    style.configure("Panel.TFrame", background=PANEL)  # plain panel colour inside a rounded card
    style.configure("Rule.TFrame", background=BORDER)  # a 1 px dividing line
    style.configure("Card.Quiet.TButton", foreground=ACCENT_TEXT, padding=(10, 7))
    style.map("Card.Quiet.TButton", foreground=[("disabled", DISABLED), ("active", TEXT)])
    style.configure("Card.Primary.TButton", padding=(16, 7), width=-7)

    _rounded_elements(style, owner, fonts)
    root.bind_class("Treeview", "<Map>", _left_headings, add="+")
    root.bind_all("<<TraverseIn>>", lambda event: _focus_ring(event, True), add="+")
    root.bind_all("<FocusOut>", lambda event: _focus_ring(event, False), add="+")
    owner.studio_fonts = fonts


def _rgb(colour):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def _mix(first, second, amount):
    return "#%02x%02x%02x" % tuple(round(a + (b - a) * amount) for a, b in zip(_rgb(first), _rgb(second)))


def _depth(px, py, width, radius, height=None):
    """How far a point lies inside a rounded rectangle (pixels), for anti-aliased edges."""
    height = height or width
    dx, dy = max(radius - px, 0.0, px - (width - radius)), max(radius - py, 0.0, py - (height - radius))
    return radius - math.hypot(dx, dy) if dx or dy else min(px, py, width - px, height - py)


def _image(owner, key, size, paint, height=None):
    """A cached see-through image (Tk 9 images keep alpha); `paint(px, py)` gives each pixel's colour and opacity."""
    cache = owner.__dict__.setdefault("studio_shapes", {})
    if key not in cache:
        height = height or size
        rows = []
        for y in range(height):
            row = []
            for x in range(size):
                colour, cover = paint(x + 0.5, y + 0.5)
                row.append("#%02x%02x%02x%02x" % (*(round(c) for c in colour), round(255 * max(0.0, min(1.0, cover)))))
            rows.append("{" + " ".join(row) + "}")
        image = tk.PhotoImage(master=owner, width=size, height=height)
        image.put(" ".join(rows))
        cache[key] = image
    return cache[key]


def _blend(first, second, amount):
    return tuple(a + (b - a) * amount for a, b in zip(first, second))


def _shape(owner, fill, size, radius, outline=None, alpha=255, fill_bottom=None, shine=0.0):
    """An anti-aliased rounded rectangle, stretched by ttk as a 9-slice image so buttons, fields, tabs and cards
    get soft corners at any size. `fill_bottom` shades it top to bottom; `outline` draws a 1 px rim; `shine`
    lightens the top edge, like light falling on a key."""
    top, bottom, edge = _rgb(fill), _rgb(fill_bottom or fill), _rgb(outline or fill)

    def paint(px, py):
        depth = _depth(px, py, size, radius)
        rim = max(0.0, min(1.0, 1.5 - depth))
        colour = _blend(_blend(top, bottom, py / size), edge, rim if outline else 0.0)
        if shine and py < radius:  # only inside the top band, which ttk never stretches
            colour = _blend(colour, (255, 255, 255), rim * shine)
        return colour, max(0.0, min(1.0, depth + 0.5)) * alpha / 255
    return _image(owner, ("shape", fill, size, radius, outline, alpha, fill_bottom, shine), size, paint)


def _tab(owner, selected, ring=None):
    """A tab: see-through, with a purple underline when open and a rounded ring for keyboard focus."""
    size, line, accent, rim = 24, 2, _rgb(ACCENT), _rgb(ring or ACCENT)

    def paint(px, py):
        if selected and py > size - line:
            return accent, 1.0
        depth = _depth(px, py, size, 6)  # the ring: a 1 px rounded outline, nothing inside it
        return rim, max(0.0, min(1.0, 1.5 - depth)) * max(0.0, min(1.0, depth + 0.5)) if ring else 0.0
    return _image(owner, ("tab", selected, ring), size, paint)


def _indicator(owner, kind, on, edge):
    """Checkbox and radio button marks: a rounded box with a tick, or a ring with a dot."""
    size, half = 18, 9.0
    base, rim = _rgb(ACCENT if on else FIELD), _rgb(edge)
    white = (255, 255, 255)

    def tick(px, py):  # distance to the two strokes of the tick
        best = 99.0
        for (ax, ay), (bx, by) in (((4.6, 9.4), (7.6, 12.4)), ((7.6, 12.4), (13.4, 5.9))):
            t = max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)))
            best = min(best, math.hypot(px - ax - t * (bx - ax), py - ay - t * (by - ay)))
        return max(0.0, min(1.0, 1.6 - best))

    def paint(px, py):
        if kind == "radio":
            depth = half - math.hypot(px - half, py - half)
            mark = max(0.0, min(1.0, 3.8 - math.hypot(px - half, py - half)))
        else:
            depth, mark = _depth(px, py, size, 5), tick(px, py)
        colour = _blend(base, rim, max(0.0, min(1.0, 1.5 - depth)))
        return _blend(colour, white, mark if on else 0.0), max(0.0, min(1.0, depth + 0.5))
    return _image(owner, ("indicator", kind, on, edge), size, paint)


def _rounded_elements(style, owner, fonts):
    def element(name, normal, *states, border=8, padding=0, **options):
        """`padding=0`: the image draws the rounded box and the style's own padding alone sets the size;
        by default ttk would add the image's border as extra padding and every control would grow."""
        if name not in style.element_names():
            style.element_create(name, "image", normal, *states, border=border, padding=padding,
                                 **{"sticky": "nsew", **options})

    def shape(fill, **options):
        return _shape(owner, fill, 24, 7, **options)

    clear = shape(BACKGROUND, alpha=0)
    ring = ACCENT_TEXT  # keyboard focus ("user1", set only when Tab reaches a control, like :focus-visible)
    element("Studio.button", shape(RAISED, outline=EDGE, shine=0.06), ("disabled", shape(PANEL, outline=BORDER)),
            ("pressed", shape(BORDER, outline=EDGE)), ("user1", shape(RAISED, outline=ring)),
            ("active", shape(RAISED_HOVER, outline="#3d3750", shine=0.08)))
    element("Studio.primary", shape(ACCENT, shine=0.22), ("disabled", shape(PANEL, outline=BORDER)),
            ("pressed", shape(ACCENT_PRESSED)), ("user1", shape(ACCENT, outline="#ffffff")),
            ("active", shape(ACCENT_HOVER, shine=0.22)))
    element("Studio.quiet", clear, ("disabled", clear), ("pressed", shape(RAISED)), ("user1", shape(BACKGROUND, outline=ring)),
            ("active", shape(PANEL)))
    element("Studio.quiet.card", clear, ("disabled", clear), ("pressed", shape(BORDER)),
            ("user1", shape(PANEL, outline=ring)), ("active", shape(RAISED)))
    element("Studio.navrow", _shape(owner, PANEL, 24, 7, alpha=0), ("active", _shape(owner, RAISED, 24, 7)))
    element("Studio.tab", _tab(owner, False), ("selected user1", _tab(owner, True, ring)), ("selected", _tab(owner, True)),
            ("user1", _tab(owner, False, ring)))
    element("Studio.client", _image(owner, ("client",), 8, lambda px, py: (_rgb(BORDER), 1.0 if py < 1 else 0.0)),
            border=(1, 1, 1, 1), padding=(0, 18, 0, 0))  # a hairline under the tabs, then space
    element("Studio.field", shape(FIELD, outline=EDGE), ("disabled", shape(BACKGROUND, outline=BORDER)),
            ("focus", shape(FIELD, outline=ACCENT)))
    element("Studio.combo", shape(FIELD, outline=EDGE), ("disabled", shape(BACKGROUND, outline=BORDER)),
            ("focus", shape(FIELD, outline=ACCENT)), padding=(1, 1, 8, 1))  # keeps the arrow off the rounded rim
    element("Studio.card", _shape(owner, PANEL, 32, 12, outline=BORDER),
            ("active", _shape(owner, "#14121c", 32, 12, outline="#4a3a80")), border=12, padding=12)
    element("Studio.badge", _shape(owner, ACCENT_SOFT, 22, 11, outline=_mix(ACCENT_SOFT, ACCENT, 0.55)), border=10)
    thumb = lambda colour: _shape(owner, colour, 10, 5)  # noqa: E731 — the scrollbar's rounded handle
    element("Studio.thumb", thumb("#2e2a3a"), ("pressed", thumb("#4a4262")), ("active", thumb("#3d3650")), border=4)
    # sliders: a thin rounded track and a round purple knob (ring for the Tab key)
    knob = lambda fill, rim: _shape(owner, fill, 20, 10, outline=rim)  # noqa: E731
    element("Studio.scale.trough", _shape(owner, "#2a2735", 12, 3), border=4, sticky="ew", height=6)
    element("Studio.scale.slider", knob(ACCENT, ACCENT_TEXT), ("disabled", knob(PANEL, BORDER)),
            ("pressed", knob(ACCENT_PRESSED, ACCENT_TEXT)), ("user1", knob(ACCENT, "#ffffff")),
            ("active", knob(ACCENT_HOVER, "#ffffff")), border=0, sticky="", width=20, height=20)
    for kind in ("check", "radio"):
        element(f"Studio.{kind}", _indicator(owner, kind, False, "#4a4360"),
                ("disabled", _indicator(owner, kind, False, BORDER)), ("selected user1", _indicator(owner, kind, True, "#ffffff")),
                ("selected", _indicator(owner, kind, True, ACCENT)), ("user1", _indicator(owner, kind, False, ring)),
                ("active", _indicator(owner, kind, False, ACCENT_HOVER)), border=0, sticky="w", width=26)

    def inside(element_name, part, children):
        return [(element_name, {"sticky": "nswe", "children": [(f"{part}.padding", {"sticky": "nswe", "children": children})]})]

    for name, element_name in (("TButton", "Studio.button"), ("Primary.TButton", "Studio.primary"),
                               ("Quiet.TButton", "Studio.quiet"), ("Card.Quiet.TButton", "Studio.quiet.card")):
        style.layout(name, inside(element_name, "Button", [("Button.label", {"sticky": "nswe"})]))
    style.layout("TEntry", inside("Studio.field", "Entry", [("Entry.textarea", {"sticky": "nswe"})]))
    style.layout("TCombobox", [("Studio.combo", {"sticky": "nswe", "children": [
        ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"expand": "1", "sticky": "nswe", "children": [("Combobox.textarea", {"sticky": "nswe"})]})]})])
    style.configure("TCombobox", background=FIELD, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, arrowsize=10)
    style.map("TCombobox", background=[("active", FIELD)], bordercolor=[], lightcolor=[])
    for kind, part in (("check", "Checkbutton"), ("radio", "Radiobutton")):
        style.layout(f"T{part}", [(f"{part}.padding", {"sticky": "nswe", "children": [
            (f"Studio.{kind}", {"side": "left", "sticky": ""}),
            (f"{part}.label", {"side": "left", "sticky": "nswe"})]})])
    style.layout("TNotebook", [("Studio.client", {"sticky": "nswe"})])
    style.layout("TNotebook.Tab", [("Studio.tab", {"sticky": "nswe", "children": [("Notebook.padding", {
        "side": "top", "sticky": "nswe", "children": [("Notebook.label", {"side": "top", "sticky": ""})]})]})])
    style.layout("Card.TFrame", [("Studio.card", {"sticky": "nswe"})])
    style.layout("NavRow.TFrame", [("Studio.navrow", {"sticky": "nswe"})])
    style.layout("Horizontal.TScale", [("Horizontal.Scale.focus", {"sticky": "nswe", "children": [
        ("Studio.scale.trough", {"sticky": "ew", "children": [("Studio.scale.slider", {"side": "left", "sticky": ""})]})]})])
    style.configure("Horizontal.TScale", background=BACKGROUND, focuscolor=BACKGROUND, sliderlength=20)
    style.configure("NavRow.TFrame", background=PANEL)
    for direction, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):  # slim, rounded and arrowless, like macOS
        style.layout(f"{direction}.TScrollbar", [(f"{direction}.Scrollbar.trough", {"sticky": sticky, "children": [
            ("Studio.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure(f"{direction}.TScrollbar", troughcolor=BACKGROUND, background=BACKGROUND, borderwidth=0,
                        bordercolor=BACKGROUND, lightcolor=BACKGROUND, darkcolor=BACKGROUND, width=10, arrowsize=10)
    # the images paint the fill; each widget's own background shows only in the rounded-off corners,
    # so it must be the colour of whatever sits behind it
    for name, behind in (("TButton", BACKGROUND), ("Primary.TButton", BACKGROUND), ("Quiet.TButton", BACKGROUND),
                         ("Card.Primary.TButton", PANEL),
                         ("Card.Quiet.TButton", PANEL), ("TEntry", BACKGROUND), ("Card.TFrame", BACKGROUND)):
        style.configure(name, background=behind)
        style.map(name, background=[], bordercolor=[], lightcolor=[], darkcolor=[])
    style.layout("Badge.TLabel", inside("Studio.badge", "Label", [("Label.label", {"sticky": "nswe"})]))
    style.configure("Badge.TLabel", foreground=ACCENT_TEXT, font=(fonts["Small"][0], 12, "bold"), padding=(7, 2),
                    anchor="center")


def on_hover(widget, enter, leave):
    """Calls `enter` when the pointer comes over the widget and `leave` once it has left it and everything
    inside it (moving onto a label inside a card isn't leaving the card)."""
    def inside():
        try:
            under = widget.winfo_containing(*widget.winfo_pointerxy())
        except (KeyError, tk.TclError):  # over a Tk-internal widget, or the window is closing
            return False
        while under is not None and under is not widget:
            under = under.master
        return under is widget

    widget.bind("<Enter>", lambda event: enter(), add="+")
    widget.bind("<Leave>", lambda event: inside() or leave(), add="+")


def hover_card(card):
    """Lights a card's border purple while the pointer is over it."""
    on_hover(card, lambda: card.state(["active"]), lambda: card.state(["!active"]))


def icon(parent, kind, size=40, background=PANEL, tile=ACCENT_SOFT, colour=ACCENT_TEXT):
    """A small rounded tile with a drawn symbol: the brand mark ("logo") or a tool's own icon."""
    canvas = tk.Canvas(parent, width=size, height=size, highlightthickness=0, borderwidth=0, background=background)
    owner = parent._root()
    if kind == "logo":
        image = _shape(owner, "#6d3df0", size, round(size * 0.28), fill_bottom="#2a0f6e")
    else:
        image = _shape(owner, tile, size, round(size * 0.28), outline=_mix(tile, ACCENT, 0.35))
    canvas.create_image(0, 0, image=image, anchor="nw")
    canvas.image = image
    s, c, line = size / 40, size / 2, max(2.0, size / 14)

    def stroke(*points, fill=colour, width=line):
        canvas.create_line(*(p * s for p in points), fill=fill, width=width, capstyle="round", joinstyle="round")

    if kind in ("logo", "wave"):
        for x, level in zip((11, 16, 20, 24, 29), (0.35, 0.7, 1.0, 0.6, 0.3)):
            stroke(x, 20 - 8 * level, x, 20 + 8 * level, fill="#ffffff" if kind == "logo" else colour)
    elif kind == "organizer":  # sorting lines, shortest last
        for y, length in ((13, 18), (20, 13), (27, 8)):
            stroke(11, y, 11 + length, y)
        stroke(29, 12, 29, 28)
        stroke(25.5, 24.5, 29, 28, 32.5, 24.5)
    elif kind == "auto_poster":  # sending upward out of a tray
        stroke(20, 25, 20, 11)
        stroke(15, 16, 20, 11, 25, 16)
        stroke(12, 23, 12, 29, 28, 29, 28, 23)
    elif kind == "project_library":  # a grid of sessions
        for x in (12, 21.5):
            for y in (12, 21.5):
                canvas.create_rectangle(x * s, y * s, (x + 6.5) * s, (y + 6.5) * s, outline=colour, width=line * 0.8)
    elif kind == "varispeed":  # a speed dial
        canvas.create_arc(10 * s, 10 * s, 30 * s, 30 * s, start=-30, extent=240, style="arc", outline=colour, width=line)
        stroke(20, 20, 26, 14)
        canvas.create_oval(c - 2.2 * s, c - 2.2 * s, c + 2.2 * s, c + 2.2 * s, fill=colour, outline="")
    elif kind == "settings":  # a studio knob: dots round its travel, the pointer at half past one
        for angle in range(-135, 136, 45):
            x, y = c + 14 * s * math.sin(math.radians(angle)), c - 14 * s * math.cos(math.radians(angle))
            canvas.create_oval(x - 1.2 * s, y - 1.2 * s, x + 1.2 * s, y + 1.2 * s, fill=colour, outline="")
        canvas.create_oval(c - 8.5 * s, c - 8.5 * s, c + 8.5 * s, c + 8.5 * s, outline=colour, width=line)
        stroke(20, 20, 24, 16)
    elif kind == "shield":  # permissions
        stroke(20, 9, 29, 12.5, 29, 20, 20, 31, 11, 20, 11, 12.5, 20, 9)
        stroke(15.5, 20, 19, 23.5, 25, 16.5)
    else:
        canvas.create_oval(c - 3 * s, c - 3 * s, c + 3 * s, c + 3 * s, fill=colour, outline="")
    return canvas


def section_title(parent, title, card=False):
    """A section heading: a small purple step number ("1  Add your media"), brand-colour dots for
    platforms ("TikTok & Instagram"), then the title. `card`: it sits inside a rounded card."""
    frame = ttk.Frame(parent, style="Panel.TFrame" if card else "TFrame")
    number, _, rest = title.partition("  ")
    if number.isdigit() and rest:
        ttk.Label(frame, text=number, style="Badge.TLabel").pack(side="left", padx=(0, 10))
        title = rest
    for name, colour in PLATFORM_COLOURS.items():
        if name in title:
            dot = tk.Canvas(frame, width=10, height=10, highlightthickness=0, background=PANEL if card else BACKGROUND)
            dot.create_oval(1, 1, 9, 9, fill=colour, outline="")
            dot.pack(side="left", padx=(0, 6))
    ttk.Label(frame, text=title, style="CardHeading.TLabel" if card else "Heading.TLabel").pack(side="left", padx=(2, 0))
    return frame


def banner(parent, title, subtitle="", eyebrow="", kind=None):
    """The strip along the top of each window: the tool's icon, its name and one line on what it does,
    over a hairline. Kept plain so the window's own content stands out."""
    apply_theme(parent)  # does nothing when the window's app is already themed
    bar = ttk.Frame(parent, style="Header.TFrame", padding=(24, 18, 24, 16))
    if kind:
        icon(bar, kind, size=42, background=HEADER).pack(side="left", padx=(0, 14))
    words = ttk.Frame(bar, style="Header.TFrame")
    words.pack(side="left", fill="x", expand=True)
    if eyebrow:
        ttk.Label(words, text=eyebrow, style="HeaderEyebrow.TLabel").pack(anchor="w", pady=(0, 2))
    ttk.Label(words, text=title, style="HeaderTitle.TLabel").pack(anchor="w")
    if subtitle:
        line = ttk.Label(words, text=subtitle, style="HeaderMuted.TLabel", justify="left")
        line.pack(anchor="w", pady=(1, 0))
        words.bind("<Configure>", lambda event: line.configure(wraplength=max(200, event.width)), add="+")  # wraps, never clips
    bar.pack(fill="x")
    ttk.Frame(parent, height=1, style="Rule.TFrame").pack(fill="x")
    return bar


def empty_state(tree, text):
    """A quiet line in the middle of a table while it has no rows, saying what will appear there."""
    label = ttk.Label(tree, text=text, style="Empty.TLabel", justify="center", anchor="center", wraplength=380)

    def check():
        try:
            if tree.get_children():
                label.place_forget()
            else:
                label.place(relx=0.5, rely=0.55, anchor="center")
        except tk.TclError:  # the window closed
            pass

    for name in ("insert", "delete", "detach", "move", "set_children"):  # re-check whenever the rows change
        def changed(*args, _method=getattr(tree, name), **options):
            result = _method(*args, **options)
            check()
            return result
        setattr(tree, name, changed)
    check()
    return label


def _focus_ring(event, shown):
    """Shows a ring on the control the Tab key moved to, and takes it off when focus leaves; mouse clicks never
    show it (the `user1` state drives the ring images)."""
    if isinstance(event.widget, ttk.Widget):
        try:
            event.widget.state(["user1" if shown else "!user1"])
        except tk.TclError:  # the window is closing
            pass


def _left_headings(event):
    """Table headings line up with their column's text instead of floating in the middle."""
    tree = event.widget
    try:
        for column in ("#0", *tree["columns"]):
            tree.heading(column, anchor="w")
    except tk.TclError:  # the table was closed before it appeared
        pass


def footer(window, variable, wraplength=900):
    """The quiet status bar along the bottom of a tool window, with a hairline above it."""
    bar = ttk.Frame(window, style="Footer.TFrame", padding=(18, 8))
    bar.pack(side="bottom", fill="x")
    ttk.Frame(window, height=1, style="Rule.TFrame").pack(side="bottom", fill="x")
    label = ttk.Label(bar, textvariable=variable, style="FooterMuted.TLabel", wraplength=wraplength, justify="left")
    label.pack(anchor="w")
    return label
