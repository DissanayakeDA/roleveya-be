import base64

import pytest

from app.core.cookies import ACCESS_COOKIE
from app.services.ai import AiRateLimitedError
from app.services.cv_ai import locate
from app.services.cv_templates import TEMPLATES, CvData, CvSection, render_cv, starter_latex
from app.services.latex import _issues_from, _resume_class_spelled_out, _with_word_shims, document_body_start
from app.services.latex_guard import LatexIssue, check_latex
from app.services.pdf_text import pdf_text
from tests.conftest import PDF_BYTES

# The preamble of a widely used pdfLaTeX resume template, which has to keep working.
PDFLATEX_RESUME = r"""\documentclass[letterpaper,11pt]{article}
\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}
\input{glyphtounicode}
\pdfgentounicode=1
\begin{document}
\href{https://github.com/ada}{github.com/ada} \textbar{} 50\% faster % a comment: \input{/etc/passwd}
\begin{itemize}
  \item Built the billing API in FastAPI and PostgreSQL for 20k users
\end{itemize}
\end{document}
"""

# A CV made from Overleaf's "Medium Length Professional CV" template, which uses its own resume class.
RESUME_CLASS_CV = r"""\documentclass[10pt]{resume}
\usepackage[left=0.4in,top=0.3in,right=0.4in,bottom=0.3in]{geometry}
\name{Ada Lovelace}
\address{Colombo}
\address{+94 77 123 4567 \\ ada@example.com} % phone and email
\begin{document}
\begin{rSection}{Experience}
\begin{rSubsection}{Analytical Engines}{2022 -- 2024}{Engineer}{Colombo}
\item Cut costs by 30\%
\end{rSubsection}
\begin{rSubsection}{Side project}{2021}{}{}
\item Built a loom
\end{rSubsection}
\end{rSection}
\end{document}
"""

CV_JSON = {
    "name": "Ada Lovelace",
    "headline": "Software Engineer",
    "contact": {
        "email": "ada@example.com",
        "phone": "+94 77 123 4567",
        "location": "Colombo",
        "links": [{"label": "github.com/ada", "url": "github.com/ada"}],
    },
    "sections": [
        {
            "title": "Experience",
            "text": None,
            "entries": [
                {
                    "heading": "Engineer",
                    "subheading": "Analytical Engines & Co",
                    "location": None,
                    "dates": "2022 – 2024",
                    "bullets": ["Cut costs by 30%"],
                }
            ],
            "items": [],
        },
        {"title": "Skills", "text": None, "entries": [], "items": ["Languages: Python, C#"]},
    ],
}


def _pdf_with_text(lines: list[str]) -> bytes:
    """A real one-page PDF with a text layer."""
    content = "BT /F1 12 Tf 72 760 Td " + " ".join(f"({line}) Tj 0 -16 Td" for line in lines) + " ET"
    objects = [
        "<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        "<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        f"<</Length {len(content)}>>stream\n{content}\nendstream",
    ]
    out, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


CV_PDF = _pdf_with_text(
    [
        "Ada Lovelace",
        "Software Engineer",
        "Experience",
        "Engineer, Analytical Engines, 2022 to 2024",
        "Cut costs by 30%",
        "Skills",
        "Languages: Python, C#",
    ]
)


# -- safety check ------------------------------------------------------------------------------


def test_templates_and_common_resume_latex_pass_the_check():
    for template_id in TEMPLATES:
        assert check_latex(starter_latex(template_id)) == []
    assert check_latex(PDFLATEX_RESUME) == []
    assert check_latex(RESUME_CLASS_CV) == []


@pytest.mark.parametrize(
    "snippet",
    [
        r"\input{/etc/passwd}",
        r"\input /etc/passwd",
        r"\input{../../.env}",
        r"\let\x\input \x{secret}",
        r"\expandafter\csname input\endcsname",
        r"^^5cinput{x}",
        r"\catcode`\@=11",
        r"\openin1=x \read1 to \x",
        r"\special{pdf:fstream @x (/etc/passwd)}",
        r"\includegraphics{photo.png}",
        r"\lstinputlisting{notes.txt}",
        r"\attachfile{x}",
        r"\ExplSyntaxOn",
        r"\UseName{@@input}",
        r"\usepackage{datatool}",
        r"\usepackage{xcolor,catchfile}",
        r"\setmainfont[Path=/usr/share/fonts/]{Secret}",
        r"\href{C:\Users\me}{x}",
    ],
)
def test_check_refuses_file_access_and_name_building(snippet):
    source = "\\documentclass{article}\n\\begin{document}\n" + snippet + "\n\\end{document}\n"
    issues = check_latex(source)
    assert issues, snippet
    assert issues[0].line == 3


def test_check_refuses_unknown_document_classes_and_packages():
    assert "document class" in check_latex("\\documentclass{evil}\n")[0].message
    assert check_latex("\\documentclass{../evil}\n")
    assert "package isn't available" in check_latex("\\usepackage[x]{datatool}")[0].message


def test_rendered_cv_escapes_text():
    cv = CvData.model_validate(CV_JSON)
    latex = render_cv(cv, "modern")
    assert r"Analytical Engines \& Co" in latex
    assert r"30\%" in latex and r"C\#" in latex
    assert "2022 -- 2024" in latex
    assert r"\href{https://github.com/ada}{github.com/ada}" in latex
    assert check_latex(latex) == []


def test_rendering_joins_short_skills_on_one_line():
    cv = CvData(name="A", sections=[CvSection(title="Skills", items=["Python", "Docker", "AWS"])])
    assert "Python, Docker, AWS" in render_cv(cv, "classic")


def test_body_start_ignores_commented_begin_document():
    source = "% put text after \\begin{document}\n\\documentclass{article}\n\\begin{document}\nHi\n"
    assert source[document_body_start(source):].strip() == "Hi"
    assert _with_word_shims(source).index("\\renewcommand{\\hfill}") > source.index("\nHi") - 5


def test_locate_tolerates_different_spacing():
    source = "\\begin{document}\n\\item Built the\n   billing API\n"
    assert locate("Built the billing API", source) == "Built the\n   billing API"
    assert locate("not in the cv at all", source) is None
    assert locate("\\item", source + "\\item again") is None  # not unique


def test_locate_tolerates_dropped_formatting_and_dashes():
    source = (
        "\\begin{document}\n"
        "  \\item Led the team as \\textbf{Project Lead}, taking a cross-platform app (Flutter, NestJS, AWS) live.\n"
        "  \\item Built tools in 2022--2024 for \\emph{five} teams\n"
        "  \\item Shipped the new \\textbf{billing service} to everyone\n"
    )
    # The model dropped \textbf and wrote the bullet as plain text: the whole line is used.
    found = locate("\\item Led the team as Project Lead, taking a cross-platform app (Flutter, NestJS, AWS) live.", source)
    assert found == source.splitlines()[1].strip()
    # An en dash for --, and no \item or full stop to take in.
    assert locate("Built tools in 2022–2024 for five teams", source) == "Built tools in 2022--2024 for \\emph{five} teams"
    # Ending inside \textbf{...} would leave a brace behind, so it isn't offered.
    assert locate("Shipped the new billing", source) is None
    assert locate("Led, the team", source) is None  # too few words to be sure


def test_compile_errors_are_read_from_tectonic_output_and_the_log():
    output = "note: Running TeX ...\nerror: cv.tex:12: Undefined control sequence\nerror: halted on potentially-recoverable error as specified\n"
    assert _issues_from(output, "") == [LatexIssue(12, "Undefined control sequence")]
    log = "! LaTeX Error: File `x.sty' not found.\n\nType X to quit.\nl.3 \\usepackage{x}\n"
    assert _issues_from("error: something failed", log)[0].line is None
    assert _issues_from("", log) == [LatexIssue(3, "LaTeX Error: File `x.sty' not found")]


def test_undefined_commands_are_named_with_the_class_that_defines_them():
    output = "error: cv.tex:19: Undefined control sequence\n"
    log = "(glyphtounicode)\n! Undefined control sequence.\nl.19 \\name\n          {Ada Lovelace}\nNo pages of output.\n"
    assert _issues_from(output, log) == [
        LatexIssue(
            19,
            "Undefined control sequence \\name. It comes from the resume document class: "
            "start with \\documentclass{resume}",
        )
    ]
    # Inside a macro, TeX shows the macro's text, cut off right after the unknown command.
    log = "! Undefined control sequence.\n\\skillline #1#2->\\textbf {#1:} #2\\badcmd \n\nl.30 \\skillline{x}{y}\n"
    assert _issues_from("error: cv.tex:30: Undefined control sequence\n", log)[0].message.endswith("\\badcmd")
    environment = _issues_from("error: cv.tex:29: LaTeX Error: Environment rSection undefined\n", "")[0]
    assert environment.message.endswith("start with \\documentclass{resume}")


def test_resume_class_is_spelled_out_for_word():
    word = _resume_class_spelled_out(RESUME_CLASS_CV)
    assert "\\title{Ada Lovelace}" in word
    assert "\\author{Colombo \\and +94 77 123 4567 | ada@example.com}" in word
    assert "\\section*{Experience}" in word and "rSection" not in word
    assert "\\textbf{Analytical Engines}\\hfill 2022 -- 2024\\\\\\emph{Engineer}\\hfill Colombo\n\\begin{itemize}" in word
    assert "\\textbf{Side project}\\hfill 2021\n\\begin{itemize}" in word
    assert word.count("\\end{itemize}") == 2
    assert _resume_class_spelled_out(PDFLATEX_RESUME) == PDFLATEX_RESUME


def test_compiler_retries_when_the_format_file_isnt_saved(monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.services import latex as latex_module

    calls = []

    def fake_run(command, cwd, **kwargs):
        calls.append(sorted(path.name for path in latex_module.Path(cwd).iterdir()))
        if len(calls) == 1:
            return _Completed(1, b"error: cannot write format file texput.fmt\n")
        latex_module.Path(cwd, "cv.pdf").write_bytes(PDF_BYTES)
        return _Completed(0, b"")

    executable = tmp_path / "tectonic.exe"
    executable.write_bytes(b"")
    monkeypatch.setattr(latex_module.subprocess, "run", fake_run)
    compiler = latex_module.LatexCompiler(get_settings().model_copy(update={"tectonic_path": str(executable)}))
    assert compiler.compile(RESUME_CLASS_CV) == PDF_BYTES
    assert len(calls) == 2
    assert "resume.cls" in calls[0] and "cv.tex" in calls[0]


class _Completed:
    def __init__(self, returncode: int, stderr: bytes):
        self.returncode, self.stderr, self.stdout = returncode, stderr, b""


def test_pdf_text_reads_the_text_layer():
    text = pdf_text(CV_PDF)
    assert "Ada Lovelace" in text and "Cut costs by 30%" in text


# -- master CVs and compiling ------------------------------------------------------------------


def test_status_lists_templates(auth_client):
    body = auth_client.get("/api/studio/status").json()
    assert body["ai"] and body["compiler"] and body["word"]
    assert [t["id"] for t in body["templates"]] == ["classic", "modern"]


def test_master_cv_lifecycle(auth_client):
    created = auth_client.post("/api/cv-documents", json={"name": "Main CV", "template": "modern"})
    assert created.status_code == 201
    doc = created.json()
    assert doc["template"] == "modern" and "\\begin{document}" in doc["latex"]

    assert [d["id"] for d in auth_client.get("/api/cv-documents").json()] == [doc["id"]]
    updated = auth_client.patch(f"/api/cv-documents/{doc['id']}", json={"latex": PDFLATEX_RESUME, "name": "Backend"})
    assert updated.json()["name"] == "Backend" and updated.json()["latex"] == PDFLATEX_RESUME

    assert auth_client.delete(f"/api/cv-documents/{doc['id']}").status_code == 204
    assert auth_client.get("/api/cv-documents").json() == []


def test_master_cv_needs_a_template_or_latex(auth_client):
    assert auth_client.post("/api/cv-documents", json={"name": "Empty"}).status_code == 422
    pasted = auth_client.post("/api/cv-documents", json={"name": "Mine", "latex": PDFLATEX_RESUME}).json()
    assert pasted["template"] is None


def test_other_users_cannot_open_a_master_cv(auth_client):
    doc = auth_client.post("/api/cv-documents", json={"name": "Main", "template": "classic"}).json()
    auth_client.cookies.set(ACCESS_COOKIE, "access-sub-2", path="/api")
    assert auth_client.get(f"/api/cv-documents/{doc['id']}").status_code == 404
    assert auth_client.get("/api/cv-documents").json() == []


def test_compile_returns_the_pdf(auth_client):
    response = auth_client.post("/api/latex/compile", json={"latex": starter_latex("classic")})
    body = response.json()
    assert response.status_code == 200 and body["ok"] is True
    assert base64.b64decode(body["pdf_base64"]) == PDF_BYTES


def test_compile_failures_are_200_with_line_numbers(auth_client):
    broken = "\\documentclass{article}\n\\begin{document}\nHello\n\\undefinedthing\n\\end{document}\n"
    body = auth_client.post("/api/latex/compile", json={"latex": broken}).json()
    assert body == {"ok": False, "pdf_base64": None, "issues": [{"line": 4, "message": "Undefined control sequence"}]}

    unsafe = auth_client.post("/api/latex/compile", json={"latex": "\\input{/etc/passwd}"}).json()
    assert unsafe["ok"] is False and "built-in file" in unsafe["issues"][0]["message"]


def test_word_download(auth_client):
    response = auth_client.post("/api/latex/docx", json={"latex": starter_latex("classic"), "filename": "Ada CV"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert 'attachment; filename="Ada CV.docx"' in response.headers["content-disposition"]


# -- PDF to LaTeX ------------------------------------------------------------------------------


def test_convert_an_uploaded_pdf(auth_client, fakes):
    fakes["ai"].replies = [CV_JSON]
    response = auth_client.post(
        "/api/cv-documents/convert",
        files={"file": ("ada.pdf", CV_PDF, "application/pdf")},
        data={"template": "classic"},
    )
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["name"] == "ada (LaTeX)" and doc["template"] == "classic"
    assert "Ada Lovelace" in doc["latex"] and r"\section*{Experience}" in doc["latex"]
    assert "Cut costs by 30%" in fakes["ai"].calls[0]["user"]


def test_convert_one_of_my_cvs(auth_client, fakes):
    cv = auth_client.post("/api/cvs", files={"file": ("cv.pdf", CV_PDF, "application/pdf")}).json()
    fakes["ai"].replies = [CV_JSON]
    response = auth_client.post("/api/cv-documents/convert", data={"cv_id": cv["id"], "template": "modern"})
    assert response.status_code == 201
    assert response.json()["template"] == "modern"


def test_convert_needs_the_ai(auth_client, fakes):
    fakes["ai"].available = False
    response = auth_client.post("/api/cv-documents/convert", files={"file": ("ada.pdf", CV_PDF, "application/pdf")})
    assert response.status_code == 503


def test_convert_refuses_a_pdf_without_text(auth_client):
    response = auth_client.post("/api/cv-documents/convert", files={"file": ("x.pdf", PDF_BYTES, "application/pdf")})
    assert response.status_code == 400


# -- tailoring ---------------------------------------------------------------------------------


DESCRIPTION = (
    "We're hiring a backend engineer. You must have 3+ years with Python and FastAPI, PostgreSQL, and Docker. "
    "Kubernetes is a plus."
)


def _analysis(find: str) -> dict:
    return {
        "summary": "A strong fit. Mention Docker if you've used it.",
        "matched_skills": ["Python", "FastAPI", "PostgreSQL", "python"],
        "missing_skills": [{"skill": "Docker", "importance": "required", "advice": "If you've used Docker, add it."}],
        "edits": [
            {
                "kind": "reword",
                "section": "Experience",
                "reason": "The post asks for FastAPI.",
                "find": find,
                "replace": "Built a FastAPI and PostgreSQL billing API serving 20k users",
            }
        ],
        "keywords": ["backend", "FastAPI"],
    }


def test_tailoring_a_cv_for_a_job(auth_client, fakes):
    job = auth_client.post("/api/jobs", json={"company": "Acme", "description": DESCRIPTION}).json()
    master = auth_client.post("/api/cv-documents", json={"name": "Main", "latex": PDFLATEX_RESUME}).json()
    base = f"/api/jobs/{job['id']}/tailoring"

    empty = auth_client.get(base).json()
    assert empty == {"draft": None, "analysis": None, "versions": []}

    draft = auth_client.post(f"{base}/draft", json={"master_id": master["id"]}).json()
    assert draft["job_id"] == job["id"] and draft["based_on_id"] == master["id"]
    assert draft["name"] == "Main for Acme"
    # Drafts aren't master CVs.
    assert [d["id"] for d in auth_client.get("/api/cv-documents").json()] == [master["id"]]

    # The AI quotes the bullet with different spacing; the edit is pointed at the real text.
    fakes["ai"].replies = [_analysis("Built the billing API in FastAPI   and PostgreSQL for 20k users")]
    analysis = auth_client.post(f"{base}/analysis", json={"latex": draft["latex"]})
    assert analysis.status_code == 200, analysis.text
    result = analysis.json()
    assert result["current"] is True
    assert result["matched_skills"] == ["Python", "FastAPI", "PostgreSQL"]
    assert result["edits"][0]["find"] == "Built the billing API in FastAPI and PostgreSQL for 20k users"
    assert "<post>" in fakes["ai"].calls[0]["user"] and "Kubernetes" in fakes["ai"].calls[0]["user"]

    # The same CV and post aren't sent again, unless asked.
    again = auth_client.post(f"{base}/analysis", json={"latex": draft["latex"]}).json()
    assert again["id"] == result["id"] and len(fakes["ai"].calls) == 1
    fakes["ai"].replies = [_analysis("x" * 30)]
    fresh = auth_client.post(f"{base}/analysis", json={"latex": draft["latex"], "refresh": True}).json()
    assert fresh["id"] != result["id"] and len(fakes["ai"].calls) == 2

    tailored = draft["latex"].replace("Built the billing API", "Built a FastAPI billing API")
    auth_client.patch(f"/api/cv-documents/{draft['id']}", json={"latex": tailored})
    state = auth_client.get(base).json()
    assert state["draft"]["latex"] == tailored
    assert state["analysis"]["current"] is False

    saved = auth_client.post(f"{base}/versions", json={"name": "Acme backend CV", "latex": tailored})
    assert saved.status_code == 201, saved.text
    version = saved.json()
    assert version["job"]["company"] == "Acme"

    job_after = auth_client.get(f"/api/jobs/{job['id']}").json()
    assert job_after["cv"]["id"] == version["cv_id"] and job_after["cv"]["version_id"] == version["id"]
    cvs = auth_client.get("/api/cvs").json()
    assert cvs[0]["version_id"] == version["id"] and cvs[0]["original_filename"] == "Acme backend CV.pdf"
    assert auth_client.get(f"/api/cvs/{version['cv_id']}/file").content == PDF_BYTES

    full = auth_client.get(f"/api/cv-versions/{version['id']}").json()
    assert full["latex"] == tailored
    assert auth_client.get(f"/api/cv-versions/{version['id']}/docx").status_code == 200
    assert [v["id"] for v in auth_client.get(base).json()["versions"]] == [version["id"]]


def test_versions_cannot_be_changed(auth_client):
    master = auth_client.post("/api/cv-documents", json={"name": "Main", "template": "classic"}).json()
    version = auth_client.post(f"/api/cv-documents/{master['id']}/versions", json={"name": "Main"}).json()
    url = f"/api/cv-versions/{version['id']}"
    assert auth_client.patch(url, json={"latex": "changed"}).status_code == 405
    assert auth_client.put(url, json={"latex": "changed"}).status_code == 405
    assert auth_client.get(url).json()["latex"] == master["latex"]


def test_saving_a_version_needs_latex_that_compiles(auth_client):
    job = auth_client.post("/api/jobs", json={"company": "Acme"}).json()
    broken = "\\documentclass{article}\n\\begin{document}\n\\undefinedthing\n\\end{document}"
    response = auth_client.post(f"/api/jobs/{job['id']}/tailoring/versions", json={"name": "x", "latex": broken})
    assert response.status_code == 422
    assert "Line 3" in response.json()["detail"]
    assert auth_client.get("/api/cvs").json() == []


def test_snapshot_of_a_master_cv(auth_client):
    master = auth_client.post("/api/cv-documents", json={"name": "Main", "template": "classic"}).json()
    response = auth_client.post(f"/api/cv-documents/{master['id']}/versions", json={"name": "Main, Oct 2026"})
    assert response.status_code == 201
    assert response.json()["job"] is None
    cv = auth_client.get("/api/cvs").json()[0]
    assert cv["is_default"] is True and cv["version_id"] == response.json()["id"]


def test_analysis_needs_a_description_and_reports_rate_limits(auth_client, fakes):
    bare = auth_client.post("/api/jobs", json={"company": "Acme"}).json()
    response = auth_client.post(f"/api/jobs/{bare['id']}/tailoring/analysis", json={"latex": PDFLATEX_RESUME})
    assert response.status_code == 400

    job = auth_client.post("/api/jobs", json={"company": "Acme", "description": DESCRIPTION}).json()
    fakes["ai"].replies = [AiRateLimitedError(30)]
    limited = auth_client.post(f"/api/jobs/{job['id']}/tailoring/analysis", json={"latex": PDFLATEX_RESUME})
    assert limited.status_code == 429
    assert "Try again in 30 seconds" in limited.json()["detail"]


def test_deleting_the_cv_deletes_the_version_and_deleting_the_job_keeps_it(auth_client):
    job = auth_client.post("/api/jobs", json={"company": "Acme", "description": DESCRIPTION}).json()
    master = auth_client.post("/api/cv-documents", json={"name": "Main", "latex": PDFLATEX_RESUME}).json()
    base = f"/api/jobs/{job['id']}/tailoring"
    draft = auth_client.post(f"{base}/draft", json={"master_id": master["id"]}).json()
    first = auth_client.post(f"{base}/versions", json={"name": "One", "latex": PDFLATEX_RESUME}).json()
    second = auth_client.post(f"{base}/versions", json={"name": "Two", "latex": PDFLATEX_RESUME + "%"}).json()

    assert auth_client.delete(f"/api/cvs/{first['cv_id']}").status_code == 204
    assert auth_client.get(f"/api/cv-versions/{first['id']}").status_code == 404

    assert auth_client.delete(f"/api/jobs/{job['id']}").status_code == 204
    kept = auth_client.get(f"/api/cv-versions/{second['id']}").json()
    assert kept["job"] is None
    assert auth_client.get(f"/api/cv-documents/{draft['id']}").status_code == 404


# -- Groq client -------------------------------------------------------------------------------


class _Reply:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code, self._body, self.headers = status_code, body, headers or {}
        self.text = str(body)

    def json(self):
        return self._body


def test_groq_request_uses_strict_json_schema(monkeypatch):
    from app.core.config import get_settings
    from app.services import ai as ai_module

    sent = {}

    def fake_post(url, json, headers, timeout):
        sent.update(url=url, body=json, headers=headers)
        return _Reply(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(ai_module.httpx, "post", fake_post)
    settings = get_settings().model_copy(update={"groq_api_key": "gsk_test", "groq_model": "openai/gpt-oss-120b"})
    client = ai_module.GroqClient(settings)
    assert client.json(system="s", user="u", schema_name="x", schema={"type": "object"}, max_tokens=10) == {"ok": True}
    assert sent["headers"]["Authorization"] == "Bearer gsk_test"
    assert sent["body"]["response_format"]["json_schema"]["strict"] is True
    assert sent["body"]["reasoning_effort"] == "low"


def test_groq_errors_are_told_apart(monkeypatch):
    from app.core.config import get_settings
    from app.services import ai as ai_module

    settings = get_settings().model_copy(update={"groq_api_key": "gsk_test", "groq_model": "llama-3.3-70b-versatile"})
    client = ai_module.GroqClient(settings)
    call = dict(system="s", user="u", schema_name="x", schema={}, max_tokens=10)

    monkeypatch.setattr(ai_module.httpx, "post", lambda *a, **k: _Reply(429, {}, {"retry-after": "12"}))
    with pytest.raises(ai_module.AiRateLimitedError) as limited:
        client.json(**call)
    assert limited.value.retry_after == 12

    monkeypatch.setattr(ai_module.httpx, "post", lambda *a, **k: _Reply(401, {}))
    with pytest.raises(ai_module.AiUnavailableError):
        client.json(**call)

    monkeypatch.setattr(ai_module.httpx, "post", lambda *a, **k: _Reply(200, {"choices": [{"message": {"content": "no"}}]}))
    with pytest.raises(ai_module.AiBadResponseError):
        client.json(**call)

    assert ai_module.GroqClient(get_settings().model_copy(update={"groq_api_key": None})).available is False
