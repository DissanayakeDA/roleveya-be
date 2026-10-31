"""Checks LaTeX from users before it is compiled on this server.

LaTeX is a programming language: a document can read files (\\input{/etc/passwd}), embed them in the PDF
(\\special, \\attachfile) or build those commands indirectly (\\csname, \\catcode, ^^ escapes). The
compiler runs with shell escape off and without the API's environment variables, and this check is the
second layer:

- Packages and document classes must be on an allow-list of common, file-free CV packages.
- Commands that read or write files, or that build command names from text, are refused.
- \\input may only load a bundled file by bare name (templates use \\input{glyphtounicode}).
- File paths (absolute, ../, drive letters) are refused wherever an argument could take one.

It is deliberately strict. A document it refuses gets a line number and a plain reason back.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LatexIssue:
    line: int | None
    message: str


ALLOWED_CLASSES = frozenset(
    {
        "article", "extarticle", "report", "extreport", "scrartcl", "scrreprt", "memoir",
        "moderncv", "altacv", "europecv", "curve",
        # Not part of TeX: our own version of the class that comes with Overleaf's popular resume template.
        "resume",
    }
)

ALLOWED_PACKAGES = frozenset(
    {
        # page, text and layout
        "geometry", "fullpage", "a4wide", "iftex", "ifxetex", "ifluatex", "ifpdf", "fontenc", "inputenc",
        "textcomp", "babel", "polyglossia", "csquotes", "hyperref", "bookmark", "url", "xcolor", "color",
        "enumitem", "titlesec", "titling", "sectsty", "fancyhdr", "lastpage", "parskip", "setspace",
        "ragged2e", "microtype", "multicol", "paracol", "tabularx", "array", "longtable", "ltablex",
        "supertabular", "booktabs", "multirow", "makecell", "colortbl", "hhline", "calc", "ifthen",
        "xifthen", "etoolbox", "xparse", "xstring", "changepage", "needspace", "soul", "ulem", "float",
        "caption", "wrapfig", "dashrule", "anyfontsize", "verbatim", "lipsum", "blindtext", "datetime2",
        "graphicx", "tikz", "pgf", "xspace",
        # maths and symbols
        "amsmath", "amssymb", "amsfonts", "mathtools", "bm", "latexsym", "marvosym", "wasysym", "pifont",
        "fontawesome", "fontawesome5", "fontawesome7", "academicons", "simpleicons",
        # fonts
        "fontspec", "lmodern", "charter", "XCharter", "sourcesanspro", "sourcesans3", "sourceserifpro",
        "sourceserif4", "sourcecodepro", "roboto", "lato", "opensans", "raleway", "FiraSans", "firamono",
        "helvet", "times", "mathptmx", "newtxtext", "newtxmath", "palatino", "mathpazo", "tgheros",
        "tgtermes", "tgpagella", "cabin", "carlito", "noto", "noto-sans", "noto-serif", "inter",
        "libertine", "libertinus", "ebgaramond", "plex-sans", "plex-serif", "merriweather", "montserrat",
        "xltxtra", "xunicode",
    }
)

# Refused outright. Grouped by why, so the message can say something useful.
_FILE_COMMANDS = {
    "read", "readline", "openin", "closein", "openout", "closeout", "write", "include", "includeonly",
    "IfFileExists", "filesize", "filemoddate", "filedump", "mdfivesum", "pdffilesize", "pdffilemoddate",
    "pdffiledump", "pdfmdfivesum", "XeTeXpicfile", "XeTeXpdffile", "XeTeXpdfpagecount", "special",
    "pdfximage", "pdfobj", "pdfimage", "pgfimage", "pgfdeclareimage", "bibliography", "addbibresource",
    "LoadClassWithOptions", "loadglsentries", "ShellEscape", "directlua", "latelua", "luaexec",
    "luadirect", "pdfprimitive", "primitive",
}
_NAME_BUILDERS = {
    "csname", "begincsname", "lastnamedcs", "scantokens", "scantextokens", "catcode", "lccode", "uccode",
    "UseName", "UseExpandableName", "ExpandArgs", "csuse", "csdef", "csgdef", "csedef", "csxdef", "cslet",
    "letcs", "csletcs", "csundef", "cspreto", "csappto", "csgpreto", "csgappto", "@nameuse", "@namedef",
    "@namelet", "ExplSyntaxOn", "ProvidesExplPackage", "ProvidesExplClass", "ProvidesExplFile",
}
# Parts of command names that mean file access (\@@input, \lstinputlisting, \includegraphics, ...).
_FILE_NAME_PARTS = ("input", "include", "import", "attachfile", "embedfile", "catchfile", "openin", "openout")
_HARMLESS = {"input", "endinput", "inputencoding", "inputlineno"}

_CONTROL_SEQUENCE = re.compile(r"\\([A-Za-z@]+|[^A-Za-z@])")
_BARE_FILE = re.compile(r"\s*(?:\{\s*([A-Za-z0-9_-]+(?:\.tex)?)\s*\}|([A-Za-z0-9_-]+)(?=[\s\\]|$))")
_PATH_PATTERNS = (
    (re.compile(r"\.\.[\\/]"), "Paths to other folders (../) aren't allowed."),
    (re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/](?!/)"), "File paths (like C:\\...) aren't allowed."),
    (re.compile(r"[{\[=\"']\s*~?/(?=[\w.])"), "File paths (like /home/...) aren't allowed."),
)
_TIKZ_PLOT_FILE = re.compile(r"\bplot\s*(?:\[[^\]]*\])?\s*file\b")


def check_latex(source: str) -> list[LatexIssue]:
    """Returns why the source can't be compiled here, or an empty list when it can."""
    text = strip_comments(source)
    issues: list[LatexIssue] = []

    def add(position: int, message: str) -> None:
        issue = LatexIssue(_line_at(text, position), message)
        if issue not in issues:
            issues.append(issue)

    if "^^" in text:
        add(text.index("^^"), "^^ character codes aren't allowed. Type the character itself instead.")

    for match in _CONTROL_SEQUENCE.finditer(text):
        name = match.group(1)
        position, end = match.start(), match.end()
        if name in _FILE_COMMANDS:
            add(position, f"\\{name} isn't allowed: it can read or write files on the server.")
        elif name in _NAME_BUILDERS:
            add(position, f"\\{name} isn't allowed here.")
        elif name == "input":
            if not _BARE_FILE.match(text, end):
                add(position, "\\input can only load a built-in file by name, such as \\input{glyphtounicode}.")
        elif name.lower() not in _HARMLESS and any(part in name.lower() for part in _FILE_NAME_PARTS):
            reason = "images can't be used yet" if "graphics" in name.lower() else "it reads files"
            add(position, f"\\{name} isn't available here: {reason}.")
        elif name in ("usepackage", "RequirePackage"):
            for package in _names_argument(text, end, add, position, name):
                if package not in ALLOWED_PACKAGES:
                    add(position, f"The {package} package isn't available here.")
        elif name in ("documentclass", "LoadClass"):
            for cls in _names_argument(text, end, add, position, name):
                if cls not in ALLOWED_CLASSES:
                    add(
                        position,
                        f"The {cls} document class isn't available here (a template's own .cls file can't be"
                        " added). Classes that work: article, resume, moderncv, altacv, report.",
                    )

    for pattern, message in _PATH_PATTERNS:
        for match in pattern.finditer(text):
            add(match.start(), message)
    for match in _TIKZ_PLOT_FILE.finditer(text):
        add(match.start(), "Plotting from a file isn't allowed.")

    return sorted(issues, key=lambda issue: issue.line or 0)[:20]


def _names_argument(text: str, start: int, add, position: int, command: str) -> list[str]:
    """Reads `[options]{a,b}` after \\usepackage or \\documentclass and returns the names."""
    index = skip_space(text, start)
    if index < len(text) and text[index] == "[":
        index = skip_group(text, index, "[", "]")
        index = skip_space(text, index)
    if index >= len(text) or text[index] != "{":
        add(position, f"\\{command} needs its name in braces, like \\{command}{{name}}.")
        return []
    close = skip_group(text, index, "{", "}")
    inner = text[index + 1 : close - 1]
    names = [part.strip() for part in inner.split(",") if part.strip()]
    if any(not re.fullmatch(r"[A-Za-z0-9-]+", part) for part in names):
        add(position, f"\\{command} names can only contain letters, digits and dashes.")
        return []
    return names


def skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def skip_group(text: str, index: int, opening: str, closing: str) -> int:
    """Index just past the group that starts at text[index], counting nested braces."""
    depth = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == opening or (char == "{" and opening != "{"):
            depth += 1
        elif char == closing or (char == "}" and closing != "}"):
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return index


def strip_comments(source: str) -> str:
    """Blanks out % comments, keeping every line (and so every line number) in place."""
    lines = []
    for line in source.split("\n"):
        backslashes = 0
        for index, char in enumerate(line):
            if char == "%" and backslashes % 2 == 0:
                line = line[:index]
                break
            backslashes = backslashes + 1 if char == "\\" else 0
        lines.append(line)
    return "\n".join(lines)


def _line_at(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1
