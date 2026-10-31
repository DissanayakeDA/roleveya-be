"""Built-in CV templates, and turning structured CV content into LaTeX with them.

Used to start a master CV from a template and to lay out a CV read from a PDF. The output uses only
standard packages (geometry, titlesec, enumitem, hyperref, xcolor), so it also compiles with pdfLaTeX
elsewhere, for example on Overleaf.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from pydantic import BaseModel, Field


class CvLink(BaseModel):
    label: str
    url: str


class CvContact(BaseModel):
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[CvLink] = Field(default_factory=list)


class CvEntry(BaseModel):
    """One job, degree or project."""

    heading: str
    subheading: str | None = None
    location: str | None = None
    dates: str | None = None
    bullets: list[str] = Field(default_factory=list)


class CvSection(BaseModel):
    title: str
    text: str | None = None
    entries: list[CvEntry] = Field(default_factory=list)
    # One-line items, e.g. "Languages: Python, TypeScript".
    items: list[str] = Field(default_factory=list)


class CvData(BaseModel):
    name: str
    headline: str | None = None
    contact: CvContact = Field(default_factory=CvContact)
    sections: list[CvSection] = Field(default_factory=list)


@dataclass(frozen=True)
class CvTemplate:
    id: str
    name: str
    description: str
    preamble: str


_SHARED = r"""\usepackage{iftex}
\ifPDFTeX
  \usepackage[T1]{fontenc}
  \usepackage[utf8]{inputenc}
  \usepackage{lmodern}
\fi
\usepackage[hidelinks]{hyperref}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage{xcolor}

\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlist[itemize]{leftmargin=1.1em, topsep=2pt, itemsep=1pt, parsep=0pt, partopsep=0pt}
"""

_CLASSIC = (
    r"""% Roleveya "Classic" CV. Everything after \begin{document} is your CV; the commands it uses are
% defined just below.
\documentclass[10pt]{article}
\usepackage[a4paper, top=1.5cm, bottom=1.5cm, left=1.8cm, right=1.8cm]{geometry}
"""
    + _SHARED
    + r"""\definecolor{rule}{HTML}{6B6B6B}

\titleformat{\section}{\large\scshape}{}{0pt}{}[\vspace{1pt}{\color{rule}\titlerule}]
\titlespacing*{\section}{0pt}{11pt}{5pt}

% \entry{Role, degree or project}{Dates}
\newcommand{\entry}[2]{\textbf{#1}\hfill #2\par}
% \entrysub{Organisation}{Location}
\newcommand{\entrysub}[2]{\textit{#1}\hfill\textit{#2}\par}
\newcommand{\entrygap}{\vspace{5pt}}
\newcommand{\sep}{\enspace\textbar\enspace}
"""
)

_MODERN = (
    r"""% Roleveya "Modern" CV. Everything after \begin{document} is your CV; the commands it uses are
% defined just below. Change the accent colour on the \definecolor line.
\documentclass[10pt]{article}
\usepackage[a4paper, top=1.4cm, bottom=1.4cm, left=1.6cm, right=1.6cm]{geometry}
"""
    + _SHARED
    + r"""\renewcommand{\familydefault}{\sfdefault}
\definecolor{accent}{HTML}{1F4FA3}
\definecolor{quiet}{HTML}{5A6272}

\titleformat{\section}{\color{accent}\bfseries\large}{}{0pt}{}[\vspace{-3pt}{\color{accent!35}\titlerule[0.8pt]}]
\titlespacing*{\section}{0pt}{12pt}{6pt}

% \entry{Role, degree or project}{Dates}
\newcommand{\entry}[2]{\textbf{#1}\hfill{\color{quiet}#2}\par}
% \entrysub{Organisation}{Location}
\newcommand{\entrysub}[2]{{\color{accent}#1}\hfill{\color{quiet}#2}\par}
\newcommand{\entrygap}{\vspace{6pt}}
\newcommand{\sep}{\enspace{\color{quiet}\textbullet}\enspace}
"""
)

TEMPLATES: dict[str, CvTemplate] = {
    "classic": CvTemplate(
        "classic", "Classic", "Serif type, centred header and small-caps section rules. Suits any field.", _CLASSIC
    ),
    "modern": CvTemplate(
        "modern", "Modern", "Sans-serif type, left-aligned header and a blue accent. Suits tech and design.", _MODERN
    ),
}

STARTER_CV = CvData(
    name="Your Name",
    headline="Software Engineer",
    contact=CvContact(
        email="you@example.com",
        phone="+94 77 123 4567",
        location="Colombo, Sri Lanka",
        links=[CvLink(label="linkedin.com/in/yourname", url="https://linkedin.com/in/yourname")],
    ),
    sections=[
        CvSection(
            title="Summary",
            text=(
                "Two or three sentences on what you do, the kind of role you want and the result you're proudest "
                "of. Tailor this paragraph for each job."
            ),
        ),
        CvSection(
            title="Experience",
            entries=[
                CvEntry(
                    heading="Software Engineer",
                    subheading="Company name",
                    location="Colombo",
                    dates="Jan 2024 -- Present",
                    bullets=[
                        "Start each point with a verb and end it with a result, e.g. cut page load time by 40%.",
                        "Name the tools you used, using the words job posts use.",
                        "Keep each point to one or two lines.",
                    ],
                ),
                CvEntry(
                    heading="Software Engineering Intern",
                    subheading="Company name",
                    location="Remote",
                    dates="Jun 2023 -- Dec 2023",
                    bullets=["What you built, who used it and what changed because of it."],
                ),
            ],
        ),
        CvSection(
            title="Education",
            entries=[
                CvEntry(
                    heading="BSc (Hons) in Computer Science",
                    subheading="University name",
                    location="Colombo",
                    dates="2020 -- 2024",
                    bullets=["Second class upper. Final-year project: one line on what it did."],
                )
            ],
        ),
        CvSection(
            title="Projects",
            entries=[
                CvEntry(
                    heading="Project name",
                    dates="2024",
                    bullets=["What it does, what you used to build it and a link if it's public."],
                )
            ],
        ),
        CvSection(
            title="Skills",
            items=[
                "Languages: Python, TypeScript, SQL",
                "Frameworks: FastAPI, Angular, React",
                "Tools: Git, Docker, AWS, PostgreSQL",
            ],
        ),
    ],
)


def render_cv(cv: CvData, template_id: str) -> str:
    template = TEMPLATES[template_id]
    body = [_header(cv, template_id)]
    for section in cv.sections:
        body.append(_section(section))
    return f"{template.preamble}\n\\begin{{document}}\n\n" + "\n\n".join(part for part in body if part) + "\n\n\\end{document}\n"


def starter_latex(template_id: str) -> str:
    return render_cv(STARTER_CV, template_id)


def _header(cv: CvData, template_id: str) -> str:
    contact = cv.contact
    parts = [escape(value) for value in (contact.email, contact.phone, contact.location) if value and value.strip()]
    if contact.email and contact.email.strip() and _looks_like_email(contact.email):
        parts[0] = f"\\href{{mailto:{_escape_url(contact.email.strip())}}}{{{escape(contact.email)}}}"
    for link in contact.links:
        url = _safe_url(link.url)
        label = escape(link.label or link.url)
        if label:
            parts.append(f"\\href{{{_escape_url(url)}}}{{{label}}}" if url else label)
    contact_line = "\\sep ".join(parts)

    name = escape(cv.name) or "Your Name"
    headline = escape(cv.headline)
    if template_id == "modern":
        lines = [f"{{\\Huge\\bfseries {name}}}\\par", "\\vspace{3pt}"]
        if headline:
            lines += [f"{{\\large\\color{{accent}} {headline}}}\\par", "\\vspace{4pt}"]
        if contact_line:
            lines.append(f"{{\\small\\color{{quiet}} {contact_line}}}\\par")
        return "\n".join(lines)

    lines = ["\\begin{center}", f"  {{\\LARGE\\scshape {name}}}\\par", "  \\vspace{3pt}"]
    if headline:
        lines += [f"  {headline}\\par", "  \\vspace{2pt}"]
    if contact_line:
        lines.append(f"  {{\\small {contact_line}}}\\par")
    lines.append("\\end{center}")
    return "\n".join(lines)


def _section(section: CvSection) -> str:
    title = escape(section.title)
    if not title:
        return ""
    lines = [f"\\section*{{{title}}}"]
    if section.text and section.text.strip():
        lines.append(escape(section.text))
    for index, entry in enumerate(section.entries):
        if index:
            lines.append("\\entrygap")
        lines.append(f"\\entry{{{escape(entry.heading)}}}{{{escape(entry.dates)}}}")
        if (entry.subheading and entry.subheading.strip()) or (entry.location and entry.location.strip()):
            lines.append(f"\\entrysub{{{escape(entry.subheading)}}}{{{escape(entry.location)}}}")
        bullets = [escape(bullet) for bullet in entry.bullets if bullet.strip()]
        if bullets:
            lines.append("\\begin{itemize}")
            lines += [f"  \\item {bullet}" for bullet in bullets]
            lines.append("\\end{itemize}")
    items = [item.strip() for item in section.items if item.strip()]
    if items:
        if all(":" not in item for item in items) and sum(len(item) for item in items) / len(items) < 30:
            # Single skills ("Python", "Docker") read better as one line than as a list.
            lines.append(", ".join(escape(item) for item in items))
        else:
            for item in items:
                label, colon, rest = item.partition(":")
                if colon and 0 < len(label) <= 40:
                    lines.append(f"\\textbf{{{escape(label)}:}} {escape(rest.strip())}\\par")
                else:
                    lines.append(f"{escape(item)}\\par")
    return "\n".join(lines)


_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_ESCAPE_PATTERN = re.compile(r"[\\&%$#_{}~^]")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def escape(text: str | None) -> str:
    """Makes plain text safe to put in a LaTeX document."""
    if not text:
        return ""
    text = _CONTROL_CHARS.sub("", " ".join(text.split()))
    # Two hyphens are an en dash in LaTeX, which is what date ranges should use anyway.
    text = text.replace("–", "--").replace("—", "---")
    return _ESCAPE_PATTERN.sub(lambda match: _ESCAPES[match.group()], text)


def _escape_url(url: str) -> str:
    return url.replace("\\", "").replace("%", r"\%").replace("#", r"\#").replace("{", "").replace("}", "")


def _safe_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not re.match(r"^[a-z]+://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc and " " not in url else None


def _looks_like_email(value: str) -> bool:
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()) is not None
