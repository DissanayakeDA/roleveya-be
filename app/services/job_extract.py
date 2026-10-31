"""Finds the company, title and description in a job post, from OCR lines or pasted text.

No AI: LinkedIn's layouts are regular enough that the details can usually be found from where they sit
and the interface text around them ("About the job", "2 weeks ago · 100 applicants", "12,345 followers").
Anything not found is left blank for the user to fill in, and the form marks what was filled so it gets
checked before saving.
"""

import re
from dataclasses import dataclass, replace
from statistics import median

from app.models import EmploymentType, WorkMode


@dataclass(frozen=True)
class TextLine:
    text: str
    # Pixel geometry from OCR. None for pasted text.
    left: float | None = None
    top: float | None = None
    right: float | None = None
    height: float | None = None

    @property
    def has_geometry(self) -> bool:
        return None not in (self.left, self.top, self.right, self.height)


@dataclass
class ExtractedJob:
    company: str | None = None
    title: str | None = None
    location: str | None = None
    work_mode: WorkMode | None = None
    employment_type: EmploymentType | None = None
    job_url: str | None = None
    source: str | None = None
    description: str | None = None


MAX_DESCRIPTION = 20_000

# -- patterns -----------------------------------------------------------------------------------

_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿]")
_SPACES = re.compile(r"[ \t   ]+")
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")
_BULLET = re.compile(r"^(?:[•·●○◦▪▫■□►▶➢➤✓✔✅☑🔹🔸🔷🔶👉⭐★✨📌➡→*\-–—]️?)\s*(?=\S)")
_TRUNCATED = re.compile(r"\s*(?:…|\.\.\.)\s*(?:see\s+)?more\s*$", re.I)
_HASHTAGS_ONLY = re.compile(r"^(?:#\w+[\s,]*)+$")
_URL = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.I)
_EMAIL_DOMAIN = re.compile(r"[\w.+-]+@([a-z0-9-]+)\.[a-z.]{2,}", re.I)

_DESCRIPTION_START = re.compile(
    r"^(?:about (?:the|this) (?:job|role|position|opportunity)|job (?:description|summary|details|overview)"
    r"|role (?:description|overview|summary)|position (?:description|summary|overview)|the role|description)\s*:?$",
    re.I,
)
_DESCRIPTION_END = re.compile(
    r"^(?:about the company|set alert for similar jobs|set alert|meet the hiring team|people also viewed"
    r"|similar jobs|more jobs\b.*|seniority level|job function|industries|referrals increase your chances.*"
    r"|show less|see less|looking for talent\??|company photos|more from this employer|how you match"
    r"|benefits found in job post|pay found in job post)$",
    re.I,
)

# "2 weeks ago", "Reposted 3 days ago", and OCR output that lost the space before the number.
_TIME_AGO = re.compile(
    r"(?:\b(?:re)?posted\s+(?:on\s+)?)?(?:(?<!\d)\d+|\ban?|\bone)\s*"
    r"(?:s|m|h|d|w|mo|yr|y|secs?|seconds?|mins?|minutes?|hrs?|hours?|days?|weeks?|wks?|months?|years?)\s+ago\b",
    re.I,
)
_APPLICANTS = re.compile(
    r"(?:\bover\s+)?(?<!\d)\d[\d,]*\s+(?:applicants?|people clicked apply|people applied)\b"
    r"|\bbe an early applicant\b|\bno longer accepting applications\b",
    re.I,
)
# Feed post header lines: "3d •", "1w • Edited •", "12,345 followers", "• 2nd", "+ Follow".
_FEED_HEADER = re.compile(
    r"^(?:\d+\s*(?:s|m|h|d|w|mo|yr|y)\b.*|[\d,.]+\s*[km]?\s+followers?|•?\s*(?:1st|2nd|3rd\+?)|\+?\s*follow|promoted)$",
    re.I,
)
_FOLLOWERS = re.compile(r"^[\d,.]+\s*[km]?\s+followers?$", re.I)

_COMPANY_LABEL = re.compile(r"^(?:company(?: name)?|employer|organi[sz]ation|hiring company)\s*[:\-–]\s*(.+)$", re.I)
_TITLE_LABEL = re.compile(
    r"^(?:job\s*title|position|role|title|vacancy|designation|job\s*role|opening)\s*[:\-–]\s*(.+)$", re.I
)
_LOCATION_LABEL = re.compile(r"^(?:location|job location|work location|based in)\s*[:\-–]\s*(.+)$", re.I)
_WORK_MODE_LABEL = re.compile(r"^(?:work(?:place)? (?:mode|type|arrangement)|work model)\s*[:\-–]?\s*(.*)$", re.I)
_EMPLOYMENT_LABEL = re.compile(r"^(?:employment|job|contract|engagement) type\s*[:\-–]?\s*(.*)$", re.I)
# LinkedIn's own sentences, from copied job pages.
_SAVE_AT = re.compile(r"^save\s+(?P<title>.+?)\s+at\s+(?P<company>.+)$", re.I)
_SEE_WHO = re.compile(r"^see who (?P<company>.+?) has hired for this role$", re.I)
_PREFERENCE_MATCH = re.compile(r"(?:workplace|job) type is ([\w-]+(?: [\w-]+)?)", re.I)

_NAME = r"[A-Z0-9][\w&.'’+-]*(?:\s+(?:[A-Z0-9][\w&.'’+-]*|&|of|and)){0,4}"
_IS_HIRING = re.compile(
    rf"(?P<c>{_NAME})\s+(?:is|are)\s+(?:now\s+|currently\s+|actively\s+)?"
    r"(?:hiring|looking for|seeking|recruiting|searching for|expanding)\b"
)
_JOIN = re.compile(
    rf"\b[Jj]oin(?:ing)?\s+(?:us\s+at\s+|(?:the|our)\s+(?:[\w-]+\s+){{0,2}}team\s+at\s+)?(?P<c>{_NAME})"
)
_AT_COMPANY = re.compile(
    rf"\b(?:[Cc]areers?|[Ll]ife|[Oo]pportunit(?:y|ies)|[Pp]osition|[Rr]ole|[Oo]pening|[Vv]acanc(?:y|ies)|[Hh]iring)"
    rf"\s+at\s+(?P<c>{_NAME})"
)
_RECRUITER = re.compile(
    r"\b(?:recruit\w*|talent|hr|human resources|people (?:partner|operations)|hiring manager|acquisition)\b", re.I
)

_ROLE_NOUN = (
    r"(?:Engineer|Developer|Programmer|Architect|Manager|Analyst|Scientist|Designer|Consultant|Specialist|Lead"
    r"|Leader|Head|Director|Officer|Executive|Associate|Intern|Trainee|Administrator|Accountant|Coordinator"
    r"|Assistant|Technician|Tester|Writer|Editor|Strategist|Representative|Agent|Advisor|Adviser|Recruiter"
    r"|Partner|Owner|Master|Marketer|Researcher|Operator|Supervisor|Controller|Auditor|Planner|Buyer|Clerk"
    r"|Cashier|Chef|Driver|Nurse|Teacher|Lecturer|Tutor|Instructor|Expert|Professional|Generalist|Translator"
    r"|Producer|Artist|Photographer|Animator|Copywriter)s?"
)
_ROLE = re.compile(
    r"(?P<t>(?:(?:[A-Z][\w/&+#.'’-]*|\.NET|C\+\+|C#|iOS|UI/UX)\s+){0,5}"
    rf"{_ROLE_NOUN}\b"
    r"(?:\s+(?:of|for)\s+[A-Z][\w&/-]*(?:\s+[A-Z][\w&/-]*){0,2})?"
    r"(?:\s*[-–]\s*[A-Z][\w&/+#.-]*(?:\s+[A-Z][\w&/+#.-]*){0,2})?"
    r"(?:\s*\([^)]{1,40}\))?"
    r"(?:\s+(?:I{1,3}|IV|[1-3]))?)"
)
_ROLE_WORD = re.compile(rf"\b{_ROLE_NOUN}\b", re.I)
_HIRING_CUE = re.compile(
    r"\b(?:hiring|looking for|seeking|vacanc(?:y|ies)|opening|opportunity|position|role of|join\b.*\bas\b|apply)\b",
    re.I,
)
# "We're hiring: Accountant", "Vacancy - QA Engineer"
_TITLE_LEAD = re.compile(
    r"^(?:(?:we(?:'|’)re|we are|now|urgently|urgent|currently)\s+)*"
    r"(?:hiring|vacancy|vacancies|job opening|opening|position)(?:\s+now)?\s*(?:for\s+)?(?:an?\s+)?[:!\-–]*\s+",
    re.I,
)
# Words a role phrase pulled from a sentence can start with that aren't part of the title.
_SENTENCE_LEAD_WORDS = {
    "exciting", "opportunity", "opportunities", "for", "a", "an", "great", "amazing", "open", "job", "jobs",
    "vacancy", "vacancies", "hiring", "now", "urgent", "urgently", "we're", "we", "are", "join", "position", "role",
    "career", "careers", "apply", "the", "our", "as",
}

_WORK_MODES = (
    (re.compile(r"\bremote\b", re.I), WorkMode.REMOTE),
    (re.compile(r"\bhybrid\b", re.I), WorkMode.HYBRID),
    (re.compile(r"\bon[\s-]?site\b|\bin[\s-]office\b|\bwork from office\b", re.I), WorkMode.ONSITE),
)
_EMPLOYMENT_TYPES = (
    (re.compile(r"\bfull[\s-]?time\b", re.I), EmploymentType.FULL_TIME),
    (re.compile(r"\bpart[\s-]?time\b", re.I), EmploymentType.PART_TIME),
    (re.compile(r"\binternship\b|\bintern\b", re.I), EmploymentType.INTERNSHIP),
    (re.compile(r"\bcontract(?:ual|or)?\b", re.I), EmploymentType.CONTRACT),
    (re.compile(r"\bfreelance\b", re.I), EmploymentType.FREELANCE),
    (re.compile(r"\btemporary\b", re.I), EmploymentType.TEMPORARY),
)

# Whole lines of LinkedIn interface text (compared case-insensitively, without trailing punctuation).
_NOISE_LINES = {
    "easy apply", "apply", "apply now", "save", "saved", "share", "show more", "show less", "see more", "see less",
    "more", "show more options", "like", "comment", "repost", "send", "follow", "following", "message", "connect",
    "report this job", "promoted", "actively recruiting", "actively reviewing applicants", "show all",
    "set alert for similar jobs", "meet the hiring team", "job search faster with premium", "try premium for free",
    "see how you compare to other applicants", "show match details", "help me stand out", "tailor my resume",
    "create cover letter", "use ai to assess how you fit", "people you can reach out to", "home", "my network", "jobs",
    "messaging", "notifications", "me", "for business", "search", "top job picks for you",
    "visible to anyone on or off linkedin", "edited", "translate", "see translation", "show translation",
    "view job", "reply", "load more comments", "most relevant", "add a comment", "copy link", "verified", "premium",
    "learn more", "dismiss", "close", "back", "next", "previous", "responses managed off linkedin",
    "job poster", "hiring team",
}
# Lines made only of these words are interface chrome ("56 comments · 12 reposts", "3d • Edited").
_NOISE_WORDS = {
    "like", "likes", "comment", "comments", "repost", "reposts", "reposted", "send", "share", "shares", "follow",
    "following", "followers", "follower", "reactions", "reaction", "views", "view", "impressions", "edited",
    "promoted", "save", "saved", "apply", "easy", "more", "see", "show", "less", "others", "k", "m", "s", "h", "d",
    "w", "mo", "yr", "yrs", "min", "mins", "st", "nd", "rd", "th", "connect", "message", "you", "translate",
    "translation", "reply", "replies", "celebrate", "support", "love", "insightful", "funny", "curious", "and",
    "by", "hirer", "responses", "managed", "off", "linkedin", "linkedln", "actively", "recruiting", "reviewing",
    "applicants", "applicant", "people", "clicked", "over", "visible", "anyone", "on", "or", "to", "premium",
    "options", "alert",
}
_NOISE_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"^matches your job preferences",
        r"^promoted by\b",
        r"^see who .+ has hired for this role$",
        r"^(?:show|see) (?:all|more|less)\b",
        r"^(?:retry|try|reactivate|unlock) premium",
        r"^\d+ (?:school alumni|connections?|employees?) work here",
        r"^your profile (?:matches|is missing)",
        r"^(?:tell me more about|how can i best position|am i a good fit)",
        r"^\d[\d,]*\s+(?:applicants?|people clicked apply)",
    )
]
_COMMON_HEADINGS = {
    "overview", "summary", "responsibilities", "key responsibilities", "requirements", "qualifications",
    "about us", "about the role", "job description", "description", "who we are", "what you'll do",
    "what we offer", "benefits", "skills", "experience", "duties", "the role", "role", "hiring", "we're hiring",
    "we are hiring", "job opening", "vacancy", "vacancies", "urgent", "apply now", "location",
}
_REJECT_COMPANY_START = {"our", "the", "this", "a", "an", "we", "us", "my", "your", "their", "you", "i", "it"}
_REJECT_COMPANY = {
    "linkedin", "linkedln", "sri lanka", "colombo", "remote", "hybrid", "on-site", "onsite", "team", "us",
}
_FREE_MAIL = {
    "gmail", "googlemail", "yahoo", "outlook", "hotmail", "live", "icloud", "me", "proton", "protonmail", "aol",
    "mail", "gmx", "yandex", "zoho", "msn",
}
# Link shorteners, social sites, form builders and applicant tracking systems: their domain isn't the employer.
_NON_COMPANY_HOSTS = {
    "linkedin", "lnkd", "bit", "forms", "google", "goo", "docs", "tinyurl", "wa", "whatsapp", "facebook", "fb",
    "instagram", "x", "twitter", "youtube", "youtu", "t", "rb", "cutt", "shorturl", "typeform", "jotform", "lever",
    "greenhouse", "workable", "smartrecruiters", "myworkdayjobs", "workday", "bamboohr", "recruitee", "breezy",
    "teamtailor", "ashbyhq", "jobvite", "icims", "zohorecruit", "topjobs", "xpress", "ikman", "indeed", "glassdoor",
}
_LOCATION_HINT = re.compile(
    r",|\b(?:remote|hybrid|on-?site|area|province|district|city|county|state|region|sri lanka|india|united|kingdom)\b",
    re.I,
)


# -- public entry points ------------------------------------------------------------------------


def lines_from_text(text: str) -> list[TextLine]:
    return [TextLine(line) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def extract_job(raw_lines: list[TextLine]) -> ExtractedJob:
    lines = [replace(line, text=_normalise(line.text)) for line in raw_lines]
    from_ocr = any(line.has_geometry for line in lines)
    if from_ocr:
        lines = _merge_rows(_main_column([line for line in lines if line.text and line.has_geometry]))
    if not any(line.text for line in lines):
        return ExtractedJob()

    texts = [line.text for line in lines]
    full_text = "\n".join(texts)
    found = ExtractedJob(job_url=_job_url(full_text), source=_source(full_text))

    content = [i for i, t in enumerate(texts) if t]
    marker = _description_marker(texts, content)
    header = [i for i in content if marker is None or i < marker]
    if marker is None:
        header = header[:12]
    meta = next(
        (i for i in header if (_TIME_AGO.search(texts[i]) or _APPLICANTS.search(texts[i])) and len(texts[i].split()) <= 25),
        None,
    )

    consumed: set[int] = set()
    title_candidates: list[str | None] = []
    company_candidates: list[str | None] = []

    # 1. Explicit labels and LinkedIn's own sentences.
    for i in content:
        text = texts[i]
        if match := _COMPANY_LABEL.match(text):
            company_candidates.append(match.group(1))
        if match := _TITLE_LABEL.match(text):
            title_candidates.append(match.group(1))
        if (match := _SAVE_AT.match(text)) and (marker is None or i < marker):
            title_candidates.append(match.group("title"))
            company_candidates.append(match.group("company"))
            consumed.add(i)
        if match := _SEE_WHO.match(text):
            company_candidates.append(match.group("company"))
        if re.fullmatch(r"about the company", text, re.I):
            company_candidates.append(_next_meaningful(texts, i))

    # 2. Job page header: the title sits right above "Location · 2 weeks ago · 100 applicants", the company
    #    above that. Older layouts put the company first on the meta line itself.
    if meta is not None:
        above = [i for i in range(meta - 1, -1, -1) if texts[i] and not _is_noise(texts[i])][:2]
        meta_company, location = _parse_meta(texts[meta])
        if above:
            title_candidates.append(texts[above[0]])
            consumed.add(above[0])
        if meta_company:
            company_candidates.append(meta_company)
        elif len(above) > 1:
            company_candidates.append(texts[above[1]])
            consumed.add(above[1])
        found.location = _clean_location(location)
        consumed.add(meta)
    elif from_ocr:
        # Cropped job page with no meta line: the title is the biggest text near the top.
        title_candidates.append(_tallest_line(lines, header))

    # 3. Sentences in the post: "Acme is hiring", "Join Acme as a ...", "Careers at Acme".
    for i in content[:40]:
        for pattern in (_IS_HIRING, _JOIN, _AT_COMPANY):
            company_candidates.extend(match.group("c") for match in pattern.finditer(texts[i]))
        if _HIRING_CUE.search(texts[i]) and (match := _ROLE.search(texts[i])):
            title_candidates.append(_drop_lead_words(match.group("t")))

    # 4. Feed posts by a company page: the page name sits above "12,345 followers". A recruiter's own post
    #    usually names the company in their headline ("Talent Acquisition at Acme").
    for i in header:
        if _FOLLOWERS.match(texts[i]):
            company_candidates.append(_previous_meaningful(texts, i))
        elif _RECRUITER.search(texts[i]) and (match := re.search(rf"\s(?:at|@)\s*({_NAME})\s*$", texts[i])):
            company_candidates.append(match.group(1))

    # 5. Short title-like lines near the top ("Senior Software Engineer (Backend)").
    for i in header:
        if _is_title_line(texts[i]):
            title_candidates.append(texts[i])
            break

    # 6. Last resorts: the email or link domain, and a short first line of pasted text. The first line wins
    #    when it's the fuller name ("Calcey Technologies" over "calcey.com").
    found.title = _first(title_candidates, _clean_title)
    domain = _company_from_domains(full_text)
    first_line = texts[content[0]]
    if not from_ocr and _could_be_company_line(first_line) and first_line != found.title:
        if domain and domain.casefold() not in first_line.casefold():
            company_candidates.append(domain)
        company_candidates.append(first_line)
    company_candidates.append(domain)

    found.company = _first(company_candidates, _clean_company)
    if found.company and found.company == _clean_company(first_line):
        consumed.add(content[0])
    if found.company and found.title and found.company.casefold() == found.title.casefold():
        found.title = None

    # Labels win over header chips for location, work mode and employment type.
    _fill_terms(found, texts, header)

    start = marker + 1 if marker is not None else _body_start(texts, content, meta)
    end = next((i for i in range(start, len(texts)) if _DESCRIPTION_END.match(texts[i])), len(texts))
    found.description = _description(lines[start:end], consumed={i - start for i in consumed if i >= start})
    return found


# -- helpers ------------------------------------------------------------------------------------


def _description_marker(texts: list[str], content: list[int]) -> int | None:
    """The "About the job" style heading the description starts after.

    LinkedIn's own "About the job" always counts. A generic heading ("Job description", "About the role")
    only counts when nothing above it reads like post text, so a post's opening paragraph isn't lost.
    """
    for position, i in enumerate(content):
        if not _DESCRIPTION_START.match(texts[i]):
            continue
        if re.match(r"about (?:the|this) job", texts[i], re.I):
            return i
        if all(len(texts[j].split()) <= 10 for j in content[:position]):
            return i
    return None


def _drop_lead_words(value: str) -> str:
    words = value.split()
    while len(words) > 1 and words[0].casefold() in _SENTENCE_LEAD_WORDS:
        words.pop(0)
    return " ".join(words)


def _normalise(text: str) -> str:
    text = _SPACES.sub(" ", _ZERO_WIDTH.sub("", text)).strip()
    text = _TRUNCATED.sub("", text)
    return _BULLET.sub("• ", text)


def _is_noise(text: str) -> bool:
    plain = text.casefold().strip(" .…:|•·-+!?")
    if not plain or not re.search(r"\w", plain):
        return True
    if plain in _NOISE_LINES or any(p.search(plain) for p in _NOISE_PATTERNS):
        return True
    words = re.findall(r"[^\W\d_]+", plain)
    if not words:
        return bool(re.fullmatch(r"[\d\s,.·•+%-]*", plain))
    return all(word in _NOISE_WORDS for word in words)


def _merge_rows(lines: list[TextLine]) -> list[TextLine]:
    """Orders OCR boxes into rows and joins boxes on a row that nearly touch (one sentence split in two)."""
    rows: list[list[TextLine]] = []
    for line in sorted(lines, key=lambda l: l.top):
        centre = line.top + line.height / 2
        row = rows[-1] if rows else None
        if row and min(l.top for l in row) <= centre <= max(l.top + l.height for l in row):
            row.append(line)
        else:
            rows.append([line])

    merged: list[TextLine] = []
    for row in rows:
        row.sort(key=lambda l: l.left)
        current = row[0]
        for line in row[1:]:
            if line.left - current.right < 1.2 * max(current.height, line.height):
                top = min(current.top, line.top)
                bottom = max(current.top + current.height, line.top + line.height)
                current = TextLine(f"{current.text} {line.text}", current.left, top, max(current.right, line.right), bottom - top)
            else:
                merged.append(current)
                current = line
        merged.append(current)
    return merged


def _main_column(lines: list[TextLine], depth: int = 0) -> list[TextLine]:
    """Keeps the column with the post when a screenshot shows a page with sidebars or a job list."""
    if len(lines) < 12 or depth >= 2:
        return lines
    left, right = min(l.left for l in lines), max(l.right for l in lines)
    width = right - left
    if width <= 0:
        return lines
    buckets = [0] * 60
    for line in lines:
        first = int((line.left - left) / width * 59)
        last = int((line.right - left) / width * 59)
        for b in range(first, last + 1):
            buckets[b] += 1
    gutter = min(range(9, 51), key=lambda b: buckets[b])
    if buckets[gutter] > max(1, len(lines) * 0.04):
        return lines
    split = left + (gutter + 0.5) * width / 59
    sides = [
        [l for l in lines if (l.left + l.right) / 2 < split],
        [l for l in lines if (l.left + l.right) / 2 >= split],
    ]
    if min(len(side) for side in sides) < 4:
        return lines
    marked = [side for side in sides if any(_DESCRIPTION_START.match(l.text) for l in side)]
    chosen = marked[0] if len(marked) == 1 else max(sides, key=lambda side: sum(len(l.text) for l in side))
    return _main_column(chosen, depth + 1)


def _tallest_line(lines: list[TextLine], indices: list[int]) -> str | None:
    sized = [lines[i] for i in indices if lines[i].height and not _is_noise(lines[i].text)]
    if len(sized) < 2:
        return None
    typical = median(l.height for l in sized)
    tallest = max(sized, key=lambda l: l.height)
    if tallest.height < 1.2 * typical or len(tallest.text.split()) > 12 or not _ROLE_WORD.search(tallest.text):
        return None
    return tallest.text


def _parse_meta(text: str) -> tuple[str | None, str | None]:
    """Splits "Company · Location · 2 weeks ago · 40 applicants" into (company, location)."""
    cut = min((m.start() for m in (_TIME_AGO.search(text), _APPLICANTS.search(text)) if m), default=len(text))
    parts = [p.strip(" ·•|-") for p in re.split(r"\s*[·•|]\s*", text[:cut]) if p.strip(" ·•|-")]
    if len(parts) >= 2 and not _LOCATION_HINT.search(parts[0]) and _LOCATION_HINT.search(parts[1]):
        return parts[0], parts[1]
    return None, parts[0] if parts else None


def _clean_location(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"\s*\((?:remote|hybrid|on-?site)\)\s*", " ", value, flags=re.I).strip(" ·•|,.-")
    if not value or _is_noise(value) or len(value) > 120 or not re.search(r"[^\W\d_]", value):
        return None
    return value


def _strip_name(value: str) -> str:
    value = _EMOJI.sub("", value)
    value = re.sub(r"\s+with verification$", "", value, flags=re.I)
    value = _SPACES.sub(" ", value).strip(" \t·•|:–—,!?\"'*-")
    if value.count("(") < value.count(")"):
        value = value.rstrip(")")
    return value


def _clean_company(value: str | None) -> str | None:
    if not value:
        return None
    value = _strip_name(value)
    words = value.split()
    if not (2 <= len(value) <= 120) or not words or len(words) > 8:
        return None
    if words[0].casefold() in _REJECT_COMPANY_START or value.casefold() in _REJECT_COMPANY:
        return None
    if _is_noise(value) or value.casefold() in _COMMON_HEADINGS or not re.search(r"[^\W\d_]", value):
        return None
    if _FOLLOWERS.match(value) or _TIME_AGO.search(value) or _APPLICANTS.search(value):
        return None
    return value


def _clean_title(value: str | None) -> str | None:
    if not value:
        return None
    value = _TITLE_LEAD.sub("", _strip_name(value)).strip()
    words = value.split()
    if not words or len(words) > 12 or len(value) > 200 or _is_noise(value):
        return None
    if value.casefold() in _COMMON_HEADINGS or value.endswith((".", ":")):
        return None
    # "Position: Full-time" names the contract, not the role.
    if len(words) <= 2 and not _ROLE_WORD.search(value) and _match_term(value, (*_EMPLOYMENT_TYPES, *_WORK_MODES)):
        return None
    return value[0].upper() + value[1:]


def _is_title_line(text: str) -> bool:
    words = text.split()
    if not (1 <= len(words) <= 8) or text.endswith((".", ":", "!", "?")) or text.startswith("• "):
        return False
    if re.search(r"\s(?:at|@)\s", text):  # a headline such as "Talent Partner at Acme"
        return False
    if not re.search(rf"\b{_ROLE_NOUN}\b", text):
        return False
    small = {"of", "and", "&", "for", "the", "in", "to", "a", "an", "with", "-", "–", "/"}
    significant = [w for w in words if w.casefold() not in small]
    capitalised = [w for w in significant if w[0].isupper() or w[0] in "(.#" or w[0].isdigit()]
    return len(capitalised) >= 0.75 * len(significant)


def _could_be_company_line(text: str) -> bool:
    """A name on its own line ("Calcey Technologies"), not the opening of a sentence."""
    words = text.split()
    if not (1 <= len(words) <= 6) or re.search(r"[.!?:]$|@|https?://", text) or _ROLE_WORD.search(text):
        return False
    if text.casefold() in _COMMON_HEADINGS or _is_noise(text):
        return False
    significant = [w for w in words if w.casefold() not in {"of", "and", "&", "the", "for", "de"}]
    capitalised = [w for w in significant if w[0].isupper() or w[0].isdigit()]
    return len(capitalised) >= 0.75 * len(significant)


def _next_meaningful(texts: list[str], index: int) -> str | None:
    return next((t for t in texts[index + 1 : index + 4] if t and not _is_noise(t)), None)


def _previous_meaningful(texts: list[str], index: int) -> str | None:
    return next((t for t in reversed(texts[max(0, index - 3) : index]) if t and not _is_noise(t)), None)


def _first(candidates: list[str | None], clean) -> str | None:
    for candidate in candidates:
        if cleaned := clean(candidate):
            return cleaned
    return None


def _company_from_domains(text: str) -> str | None:
    labels = [m.group(1) for m in _EMAIL_DOMAIN.finditer(text)]
    for url in _URL.findall(text):
        host = re.sub(r"^https?://(?:www\d?\.)?", "", url, flags=re.I).split("/")[0].split(":")[0]
        parts = host.split(".")
        if len(parts) >= 2:
            # careers.acme.com -> acme; acme.co.uk -> acme
            labels.append(parts[-3] if len(parts) >= 3 and len(parts[-2]) <= 3 else parts[-2])
    for label in labels:
        lowered = label.casefold()
        if lowered in _FREE_MAIL or lowered in _NON_COMPANY_HOSTS or len(label) < 2:
            continue
        # Prefer the spelling used in the post ("WSO2", not "Wso2").
        spelled = re.search(rf"\b{re.escape(label)}\b", re.sub(r"\S*@\S*|https?://\S+", "", text), re.I)
        return spelled.group(0) if spelled else label[:1].upper() + label[1:]
    return None


def _job_url(text: str) -> str | None:
    urls = [u.rstrip(".,;:!?") for u in _URL.findall(text)]
    urls = [u for u in urls if len(u) <= 2048]
    if not urls:
        return None
    preferred = [u for u in urls if re.search(r"linkedin\.com/jobs/|/jobs?/|/careers?/|apply|vacanc", u, re.I)]
    return (preferred or urls)[0]


def _source(text: str) -> str | None:
    lowered = text.casefold()
    # OCR often reads LinkedIn as "Linkedln".
    if re.search(r"linked[il1]n|easy apply|promoted by hirer|\bfollowers\b|\bapplicants\b", lowered):
        return "LinkedIn"
    if "indeed" in lowered:
        return "Indeed"
    if "glassdoor" in lowered:
        return "Glassdoor"
    return None


def _match_term(text: str, terms):
    return next((value for pattern, value in terms if pattern.search(text)), None)


def _fill_terms(found: ExtractedJob, texts: list[str], header: list[int]) -> None:
    for i, text in enumerate(texts):
        if not text:
            continue
        if (match := _LOCATION_LABEL.match(text)) and not found.location:
            found.location = _clean_location(match.group(1))
            found.work_mode = found.work_mode or _match_term(match.group(1), _WORK_MODES)
        if match := _WORK_MODE_LABEL.match(text):
            value = match.group(1) or (texts[i + 1] if i + 1 < len(texts) else "")
            found.work_mode = _match_term(value, _WORK_MODES) or found.work_mode
        if match := _EMPLOYMENT_LABEL.match(text):
            value = match.group(1) or (texts[i + 1] if i + 1 < len(texts) else "")
            found.employment_type = _match_term(value, _EMPLOYMENT_TYPES) or found.employment_type
        for match in _PREFERENCE_MATCH.finditer(text):
            found.work_mode = found.work_mode or _match_term(match.group(1), _WORK_MODES)
            found.employment_type = found.employment_type or _match_term(match.group(1), _EMPLOYMENT_TYPES)

    # Header chips ("Hybrid", "Full-time") and the title itself ("Intern (Remote)").
    short_header = [texts[i] for i in header if len(texts[i].split()) <= 6]
    for text in [found.title or "", *short_header]:
        found.work_mode = found.work_mode or _match_term(text, _WORK_MODES)
        found.employment_type = found.employment_type or _match_term(text, _EMPLOYMENT_TYPES)


def _body_start(texts: list[str], content: list[int], meta: int | None) -> int:
    """Where the post text begins when there's no "About the job" heading."""
    start = meta + 1 if meta is not None else 0
    for i in content[:8]:
        if _FEED_HEADER.match(texts[i]):
            start = max(start, i + 1)
    return start


def _looks_like_heading(text: str) -> bool:
    words = text.split()
    return 0 < len(words) <= 5 and not re.search(r"[.,;]$", text) and text[:1].isupper() and not text.startswith("• ")


def _description(lines: list[TextLine], consumed: set[int]) -> str | None:
    body = [
        line
        for i, line in enumerate(lines)
        if i not in consumed and (not line.text or not (_is_noise(line.text) or _HASHTAGS_ONLY.match(line.text)))
    ]
    if any(line.has_geometry for line in body):
        text = _reflow([line for line in body if line.text])
    else:
        text = "\n".join(line.text for line in body)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_DESCRIPTION] or None


def _reflow(lines: list[TextLine]) -> str:
    """Rebuilds paragraphs and bullet lists from OCR lines, which break wherever the screen wrapped."""
    if not lines:
        return ""
    typical = median(l.height for l in lines)
    left_edge = min(l.left for l in lines)
    full_width = max(l.right for l in lines) - left_edge
    out: list[str] = [lines[0].text]
    for prev, line in zip(lines, lines[1:]):
        gap = line.top - (prev.top + prev.height)
        ended_early = (prev.right - left_edge) < 0.75 * full_width
        if gap > 0.75 * typical:
            out += ["", line.text]
        elif line.text.startswith("• ") or ended_early or prev.text.endswith(":") or _looks_like_heading(prev.text):
            out.append(line.text)
        else:
            out[-1] = f"{out[-1]} {line.text}"
    return "\n".join(out)
