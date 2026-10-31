"""Compiles LaTeX to PDF (Tectonic) and converts it to Word (pandoc), on this server.

Tectonic is a single-file TeX engine (XeTeX) that downloads the packages a document needs the first time
they're used and caches them, so the first compile of a new template is slow (tens of seconds) and later
ones take a second or two. Install it from https://tectonic-typesetting.github.io and either put it on
PATH, place it in roleveya-be/.tools/, or set TECTONIC_PATH.

Every compile runs in its own temporary folder, with shell escape off (--untrusted), a time limit, and
none of the API's environment variables, after app.services.latex_guard has accepted the source.
"""

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from app.core.config import Settings
from app.services.latex_guard import LatexIssue, check_latex, skip_group, skip_space, strip_comments

logger = logging.getLogger(__name__)

TOOLS_DIR = Path(__file__).resolve().parents[2] / ".tools"
# Document classes that popular templates ship as their own .cls file rather than being part of TeX.
CLASS_DIR = Path(__file__).resolve().parent / "tex"
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PARALLEL = 2

# Common CV templates are written for pdfLaTeX and set pdfTeX-only registers (\pdfgentounicode=1 after
# \input{glyphtounicode}). XeTeX doesn't have them, so they're defined as harmless stand-ins. The stand-ins
# go on the same line as the document's first line, so error line numbers still match the user's text.
_XETEX_SHIMS = "".join(
    rf"\ifdefined\{name}\else\newcount\{name}\fi"
    for name in ("pdfgentounicode", "pdfminorversion", "pdfcompresslevel", "pdfobjcompresslevel")
) + r"\ifdefined\pdfglyphtounicode\else\def\pdfglyphtounicode#1#2{}\fi"

# pandoc drops layout-only commands, which would run dates into job titles in the Word copy.
_WORD_SHIMS = "\n".join(
    (
        r"\renewcommand{\hfill}{ | }",
        r"\renewcommand{\textbar}{|}",
        r"\renewcommand{\enspace}{ }",
        r"\renewcommand{\quad}{ }",
        r"\renewcommand{\qquad}{ }",
        "\\renewcommand{\\textbullet}{•}",
    )
)

# Passed through to the compiler. Everything else (database URL, API keys) is left out.
_ENV_KEEP = (
    "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA",
    "APPDATA", "XDG_CACHE_HOME", "TECTONIC_CACHE_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY",
    "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy", "LANG", "LC_ALL",
)

_TECTONIC_ISSUE = re.compile(r"^(?:error|warning): (?:.*[\\/])?(?P<file>[^\\/:\n]+):(?P<line>\d+): (?P<message>.+)$", re.M)
_TECTONIC_ERROR = re.compile(r"^error: (?P<message>.+)$", re.M)
_LOG_ERROR = re.compile(r"^! (?P<message>.+)$", re.M)
_LOG_LINE = re.compile(r"^l\.(\d+)", re.M)
# TeX breaks the line after "! Undefined control sequence." right after the command it didn't know.
_LOG_UNDEFINED = re.compile(r"^! Undefined control sequence\.\r?\n(?P<context>.*)$", re.M)
_LAST_COMMAND = re.compile(r"(\\(?:[A-Za-z@]+|.))\s*$")
_UNDEFINED_ENVIRONMENT = re.compile(r"Environment (\w+) undefined")
# Commands that only a particular document class defines, so the error can say which class to use.
_DEFINED_BY = {
    "name": "resume", "address": "resume", "rSection": "resume", "rSubsection": "resume",
    "moderncvstyle": "moderncv", "moderncvcolor": "moderncv", "cventry": "moderncv", "cvitem": "moderncv",
}
_FORMAT_NOT_SAVED = "cannot write format file"

_RESUME_CLASS = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{\s*resume\s*\}")
_RESUME_HEADING = re.compile(r"\\(name|address)(?![A-Za-z@])")
_RESUME_ENVIRONMENT = re.compile(r"\\(begin|end)\s*\{(rSection|rSubsection)\}")
_LINE_BREAK = re.compile(r"\s*\\\\\*?(?:\s*\[[^\]]*\])?\s*")


class LatexUnavailableError(Exception):
    """The compiler isn't installed or couldn't be run."""


class LatexBusyError(Exception):
    pass


class LatexCompileError(Exception):
    def __init__(self, issues: list[LatexIssue]):
        super().__init__("LaTeX compile failed")
        self.issues = issues


class WordUnavailableError(Exception):
    pass


class LatexCompiler:
    def __init__(self, settings: Settings):
        self._configured = settings.tectonic_path
        self._timeout = settings.latex_timeout_seconds
        self._slots = threading.BoundedSemaphore(MAX_PARALLEL)
        # The preview and "save version" compile the same source back to back.
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._cache_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._executable() is not None

    def compile(self, source: str) -> bytes:
        """Returns the PDF. Raises LatexCompileError with line-numbered issues when the source is refused or fails."""
        issues = check_latex(source)
        if issues:
            raise LatexCompileError(issues)

        key = hashlib.sha256(source.encode("utf-8")).hexdigest()
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

        executable = self._executable()
        if executable is None:
            raise LatexUnavailableError()
        if not self._slots.acquire(timeout=30):
            raise LatexBusyError()
        try:
            pdf = self._run(executable, source)
        finally:
            self._slots.release()

        with self._cache_lock:
            self._cache[key] = pdf
            while len(self._cache) > 8:
                self._cache.popitem(last=False)
        return pdf

    def _run(self, executable: str, source: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="roleveya-tex-") as folder:
            tex = Path(folder, "cv.tex")
            tex.write_text(_XETEX_SHIMS + source.lstrip("﻿"), encoding="utf-8")
            for cls in CLASS_DIR.glob("*.cls"):
                shutil.copyfile(cls, Path(folder, cls.name))
            command = [executable, "-X", "compile", "--untrusted", "--keep-logs", "--outdir", folder, str(tex)]
            returncode, output = self._tectonic(command, folder)
            # Tectonic builds a format file on its first compile and caches it. When two compiles build it at
            # the same time, saving it can fail; by then the other one has cached it, so a second try works.
            if returncode != 0 and _FORMAT_NOT_SAVED in output:
                returncode, output = self._tectonic(command, folder)

            pdf_path = Path(folder, "cv.pdf")
            if returncode != 0 or not pdf_path.exists():
                log_path = Path(folder, "cv.log")
                log = log_path.read_text("utf-8", "replace") if log_path.exists() else ""
                raise LatexCompileError(_issues_from(output, log))
            if pdf_path.stat().st_size > MAX_PDF_BYTES:
                raise LatexCompileError([LatexIssue(None, "The PDF came out larger than 10 MB.")])
            return pdf_path.read_bytes()

    def _tectonic(self, command: list[str], folder: str) -> tuple[int, str]:
        try:
            result = subprocess.run(command, cwd=folder, env=_child_env(), capture_output=True, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            raise LatexCompileError(
                [LatexIssue(None, f"Compiling took longer than {self._timeout} seconds and was stopped.")]
            )
        except OSError as exc:
            logger.warning("Tectonic couldn't be started: %s", type(exc).__name__)
            raise LatexUnavailableError() from exc
        return result.returncode, (result.stderr + b"\n" + result.stdout).decode("utf-8", "replace")

    def _executable(self) -> str | None:
        if self._configured:
            path = Path(self._configured)
            if path.is_file():
                return str(path)
            return shutil.which(self._configured)
        found = shutil.which("tectonic")
        if found:
            return found
        for name in ("tectonic.exe", "tectonic"):
            candidate = TOOLS_DIR / name
            if candidate.is_file():
                return str(candidate)
        return None


class WordConverter:
    """LaTeX to .docx with pandoc. Keeps the text and structure, not the exact layout."""

    def __init__(self, timeout: int = 60):
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return self._executable() is not None

    def convert(self, source: str) -> bytes:
        issues = check_latex(source)
        if issues:
            raise LatexCompileError(issues)
        executable = self._executable()
        if executable is None:
            raise WordUnavailableError()
        with tempfile.TemporaryDirectory(prefix="roleveya-docx-") as folder:
            tex, docx = Path(folder, "cv.tex"), Path(folder, "cv.docx")
            tex.write_text(_with_word_shims(_resume_class_spelled_out(source)), encoding="utf-8")
            # --sandbox stops pandoc reading any file other than the input (\input, images, ...).
            command = [executable, "--sandbox", "-f", "latex", "-t", "docx", "-o", str(docx), str(tex)]
            try:
                result = subprocess.run(
                    command, cwd=folder, env=_child_env(), capture_output=True, timeout=self._timeout
                )
            except subprocess.TimeoutExpired:
                raise LatexCompileError([LatexIssue(None, "Making the Word file took too long and was stopped.")])
            except OSError as exc:
                logger.warning("pandoc couldn't be started: %s", type(exc).__name__)
                raise WordUnavailableError() from exc
            if result.returncode != 0 or not docx.exists():
                message = result.stderr.decode("utf-8", "replace").strip().splitlines()
                raise LatexCompileError([LatexIssue(None, message[-1] if message else "The Word file couldn't be made.")])
            return docx.read_bytes()

    @staticmethod
    def _executable() -> str | None:
        try:
            import pypandoc

            return pypandoc.get_pandoc_path()
        except (ImportError, OSError):
            return shutil.which("pandoc")


def document_body_start(source: str) -> int:
    """Index just after \\begin{document}, ignoring any inside % comments; 0 when there isn't one."""
    offset = 0
    for line in source.split("\n"):
        visible = strip_comments(line)
        position = visible.find("\\begin{document}")
        if position >= 0:
            return offset + position + len("\\begin{document}")
        offset += len(line) + 1
    return 0


def _with_word_shims(source: str) -> str:
    start = document_body_start(source)
    if start == 0:
        return source
    return f"{source[:start]}\n{_WORD_SHIMS}\n{source[start:]}"


def _resume_class_spelled_out(source: str) -> str:
    """pandoc reads only the .tex file, not app/services/tex/resume.cls, so for documents that use that class
    the name and address lines become the Word title block, and its sections and entries plain LaTeX."""
    text = strip_comments(source)
    start = document_body_start(text)
    if start == 0 or not _RESUME_CLASS.search(text, 0, start):
        return source
    preamble, body = text[: start - len("\\begin{document}")], text[start:]

    heading: dict[str, list[str]] = {"name": [], "address": []}
    for match in _RESUME_HEADING.finditer(preamble):
        heading[match.group(1)] += _arguments(preamble, match.end(), 1)[0]
    title = [f"\\title{{{name}}}" for name in heading["name"][-1:]]
    if heading["address"]:
        lines = " \\and ".join(_LINE_BREAK.sub(" | ", line) for line in heading["address"])
        title.append(f"\\author{{{lines}}}")

    pieces, index = [], 0
    for match in _RESUME_ENVIRONMENT.finditer(body):
        if match.start() < index:
            continue
        pieces.append(body[index : match.start()])
        action, environment = match.groups()
        index = match.end()
        if action == "end":
            pieces.append("" if environment == "rSection" else "\\end{itemize}")
        elif environment == "rSection":
            values, index = _arguments(body, index, 1)
            pieces.append(f"\\section*{{{values[0] if values else ''}}}")
        else:
            values, index = _arguments(body, index, 4)
            organisation, dates, role, place = values + [""] * (4 - len(values))
            entry = f"\\textbf{{{organisation}}}\\hfill {dates}"
            if role.strip():
                entry += f"\\\\\\emph{{{role}}}\\hfill {place}"
            pieces.append(f"{entry}\n\\begin{{itemize}}")
    pieces.append(body[index:])
    return "\n".join([preamble, *title, "\\begin{document}"]) + "".join(pieces)


def _arguments(text: str, index: int, count: int) -> tuple[list[str], int]:
    """Reads up to `count` {...} arguments from text[index]; returns them and the index after the last."""
    values = []
    for _ in range(count):
        index = skip_space(text, index)
        if index >= len(text) or text[index] != "{":
            break
        end = skip_group(text, index, "{", "}")
        values.append(text[index + 1 : end - 1])
        index = end
    return values, index


def _child_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
    env["TECTONIC_UNTRUSTED_MODE"] = "1"
    return env


def _issues_from(output: str, log: str) -> list[LatexIssue]:
    """Pulls readable errors out of Tectonic's output, falling back to the TeX log."""
    issues: list[LatexIssue] = []

    def add(line: int | None, message: str) -> None:
        issue = LatexIssue(line, _explained(message.strip().rstrip("."), log))
        if issue.message and issue not in issues:
            issues.append(issue)

    for match in _TECTONIC_ISSUE.finditer(output):
        if match.group(0).startswith("error") and match.group("file") == "cv.tex":
            add(int(match.group("line")), match.group("message"))
        elif match.group(0).startswith("error"):
            add(None, f"{match.group('file')}: {match.group('message')}")
    if not issues:
        for match in _TECTONIC_ERROR.finditer(output):
            message = match.group("message")
            if "halted on potentially-recoverable error" not in message:
                add(None, message)
    if not issues and log:
        for match in _LOG_ERROR.finditer(log):
            following = _LOG_LINE.search(log, match.end(), match.end() + 600)
            add(int(following.group(1)) if following else None, match.group("message"))
    if not issues:
        tail = [line for line in output.strip().splitlines() if line.strip()][-3:]
        add(None, " ".join(tail) or "The LaTeX couldn't be compiled")
    return issues[:10]


def _explained(message: str, log: str) -> str:
    """Adds the command TeX didn't know to "Undefined control sequence", and the class that defines it."""
    if message == "Undefined control sequence":
        context = _LOG_UNDEFINED.search(log)
        command = _LAST_COMMAND.search(context.group("context")) if context else None
        if command is None:
            return message
        message = f"Undefined control sequence {command.group(1)}"
        name = command.group(1)[1:]
    else:
        environment = _UNDEFINED_ENVIRONMENT.search(message)
        if environment is None:
            return message
        name = environment.group(1)
    cls = _DEFINED_BY.get(name)
    if cls is None:
        return message
    return f"{message}. It comes from the {cls} document class: start with \\documentclass{{{cls}}}"
