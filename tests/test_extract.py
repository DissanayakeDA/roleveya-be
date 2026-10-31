from app.models import EmploymentType, WorkMode
from app.services.job_extract import TextLine, extract_job, lines_from_text
from tests.conftest import PNG_BYTES


def ocr_lines(rows):
    """(text, left, top, height) rows; the right edge is estimated from the text length, as OCR boxes are."""
    return [TextLine(text, left, top, left + len(text) * height * 0.42, height) for text, left, top, height in rows]


LINKEDIN_JOB_PAGE = ocr_lines(
    [
        ("Dialog Axiata PLC", 89, 43, 24),
        ("Senior Software Engineer (Backend)", 29, 110, 30),
        ("Colombo, Western Province, Sri Lanka · 2 weeks ago · Over 100 applicants", 29, 153, 23),
        ("Promoted by hirer · Responses managed off Linkedln", 29, 184, 23),
        ("Hybrid", 36, 230, 28),
        ("Full-time", 140, 233, 21),
        ("Easy Apply", 53, 296, 25),
        ("Save", 202, 296, 24),
        ("About the job", 27, 368, 33),
        ("We are looking for a Senior Software Engineer to join our platform team and help", 29, 419, 23),
        ("build the services that power our digital products.", 28, 451, 23),
        ("Key Responsibilities", 28, 498, 26),
        ("• Design, build and maintain scalable backend services in Python and Go", 28, 531, 23),
        ("• Review code and mentor junior engineers", 27, 563, 24),
        ("Show more", 29, 829, 21),
    ]
)

LINKEDIN_FEED_POST = ocr_lines(
    [
        ("WSO2", 92, 30, 20),
        ("+ Follow", 758, 29, 28),
        ("98,765 followers", 91, 50, 22),
        ("3d • Edited •", 91, 72, 20),
        ("We're hiring! Join WSO2 as an Associate Technical Lead in our", 22, 113, 24),
        ("Integration team. You'll work on open source products used by", 23, 143, 23),
        ("enterprises around the world.", 22, 172, 25),
        ("What you'll do:", 23, 218, 22),
        ("- Lead a small team of engineers", 23, 247, 23),
        ("Apply here: https://wso2.com/careers/atl-integration", 25, 324, 19),
        ("#hiring #srilanka", 21, 350, 26),
        ("1,234 · 56 comments · 12 reposts", 21, 399, 22),
        ("Like", 57, 445, 22),
        ("Comment", 268, 445, 23),
        ("Repost", 477, 445, 24),
        ("Send", 687, 445, 22),
    ]
)


def test_job_page_screenshot():
    found = extract_job(LINKEDIN_JOB_PAGE)
    assert found.company == "Dialog Axiata PLC"
    assert found.title == "Senior Software Engineer (Backend)"
    assert found.location == "Colombo, Western Province, Sri Lanka"
    assert found.work_mode == WorkMode.HYBRID
    assert found.employment_type == EmploymentType.FULL_TIME
    assert found.source == "LinkedIn"
    # Wrapped lines are rejoined; headings and bullets keep their own lines; interface text is dropped.
    assert found.description == (
        "We are looking for a Senior Software Engineer to join our platform team and help build the services "
        "that power our digital products.\n\n"
        "Key Responsibilities\n"
        "• Design, build and maintain scalable backend services in Python and Go\n"
        "• Review code and mentor junior engineers"
    )


def test_feed_post_screenshot():
    found = extract_job(LINKEDIN_FEED_POST)
    assert found.company == "WSO2"
    assert found.title == "Associate Technical Lead"
    assert found.job_url == "https://wso2.com/careers/atl-integration"
    assert found.description.startswith("We're hiring! Join WSO2 as an Associate Technical Lead in our Integration team.")
    assert "• Lead a small team of engineers" in found.description
    for chrome in ("followers", "Follow", "comments", "Repost", "#hiring", "3d"):
        assert chrome not in found.description


def test_screenshot_of_job_search_keeps_the_job_column():
    job_list = ocr_lines(
        [(text, 40, 80 + i * 40, 18) for i, text in enumerate(
            ["Data Analyst", "Hemas Holdings", "Colombo (On-site)", "QA Engineer", "Sysco LABS", "Colombo (Hybrid)",
             "DevOps Engineer", "IFS", "Colombo (Hybrid)", "Product Designer", "Calcey", "Colombo (Remote)"]
        )]
    )
    details = [
        TextLine(line.text, line.left + 640, line.top + 40, line.right + 640, line.height) for line in LINKEDIN_JOB_PAGE
    ]
    found = extract_job(job_list + details)
    assert found.company == "Dialog Axiata PLC"
    assert found.title == "Senior Software Engineer (Backend)"
    assert "Hemas" not in found.description and "Sysco" not in found.description


def test_pasted_linkedin_job_page():
    found = extract_job(
        lines_from_text(
            """Dialog Axiata PLC
Share
Show more options
Senior Software Engineer (Backend)
Colombo, Western Province, Sri Lanka · Reposted 2 weeks ago · Over 100 people clicked apply
Promoted by hirer · Responses managed off LinkedIn

Hybrid
Matches your job preferences, workplace type is Hybrid.

Full-time
Matches your job preferences, job type is Full-time.

Easy Apply
Save
Save Senior Software Engineer (Backend)  at Dialog Axiata PLC
Use AI to assess how you fit
About the job
We are looking for a Senior Software Engineer to join our platform team.

Key Responsibilities
- Design and build backend services
- Mentor junior engineers

Show more
Set alert for similar jobs
Software Engineer, Colombo
About the company
Dialog Axiata PLC
345,678 followers"""
        )
    )
    assert found.company == "Dialog Axiata PLC"
    assert found.title == "Senior Software Engineer (Backend)"
    assert found.location == "Colombo, Western Province, Sri Lanka"
    assert found.work_mode == WorkMode.HYBRID
    assert found.employment_type == EmploymentType.FULL_TIME
    assert found.description == (
        "We are looking for a Senior Software Engineer to join our platform team.\n\n"
        "Key Responsibilities\n"
        "• Design and build backend services\n"
        "• Mentor junior engineers"
    )


def test_pasted_feed_post_by_a_recruiter():
    found = extract_job(
        lines_from_text(
            """Nadeesha Perera
• 2nd
Talent Acquisition Partner at Sysco LABS Sri Lanka
1w •

Sysco LABS is hiring Software Engineers! 🚀
Work on platforms used by thousands of restaurants.
#hiring
…more
Like
Comment"""
        )
    )
    assert found.company == "Sysco LABS"
    assert found.title == "Software Engineers"
    assert found.description == (
        "Sysco LABS is hiring Software Engineers! 🚀\nWork on platforms used by thousands of restaurants."
    )


def test_pasted_description_with_company_on_the_first_line():
    found = extract_job(
        lines_from_text(
            """Calcey Technologies
We are hiring a QA Engineer to join our Colombo office.

Requirements:
- 2+ years of manual and automated testing
Send your CV to careers@calcey.com"""
        )
    )
    assert found.company == "Calcey Technologies"
    assert found.title == "QA Engineer"
    assert found.description.startswith("We are hiring a QA Engineer")  # the company line isn't repeated


def test_labelled_fields():
    found = extract_job(
        lines_from_text(
            """Job Title: Accountant
Company: Hayleys PLC
Location: Kandy (On-site)
Employment type: Contract

We are seeking an experienced accountant to manage month-end close."""
        )
    )
    assert found.company == "Hayleys PLC"
    assert found.title == "Accountant"
    assert found.location == "Kandy"
    assert found.work_mode == WorkMode.ONSITE
    assert found.employment_type == EmploymentType.CONTRACT


def test_company_from_email_domain_uses_the_posts_spelling():
    found = extract_job(lines_from_text("We need a Backend Developer with Go experience.\nApply: jobs@wso2.com - WSO2 team"))
    assert found.company == "WSO2"
    found = extract_job(lines_from_text("Send your CV to someone@gmail.com"))
    assert found.company is None


def test_nothing_found_leaves_fields_empty():
    found = extract_job([])
    assert found.company is None and found.title is None and found.description is None
    found = extract_job(lines_from_text("Like\nComment\nRepost\nSend"))
    assert found.description is None and found.company is None


# -- API ---------------------------------------------------------------------------------------


def test_extract_from_text_endpoint(auth_client):
    response = auth_client.post("/api/jobs/extract/text", json={"text": "WSO2 is hiring a DevOps Engineer.\nApply now"})
    assert response.status_code == 200
    body = response.json()
    assert body["found_text"] is True
    assert body["company"] == "WSO2"
    assert body["title"] == "DevOps Engineer"


def test_extract_from_screenshot_endpoint(auth_client, fakes):
    fakes["ocr"].lines = LINKEDIN_JOB_PAGE
    response = auth_client.post("/api/jobs/extract/screenshot", files={"file": ("shot.png", PNG_BYTES, "image/png")})
    assert response.status_code == 200
    assert response.json()["company"] == "Dialog Axiata PLC"
    assert response.json()["work_mode"] == "hybrid"

    fakes["ocr"].lines = []
    response = auth_client.post("/api/jobs/extract/screenshot", files={"file": ("blank.png", PNG_BYTES, "image/png")})
    assert response.json() == {
        "found_text": False, "company": None, "title": None, "location": None, "work_mode": None,
        "employment_type": None, "job_url": None, "source": None, "description": None,
    }


def test_extract_screenshot_rejects_non_images(auth_client):
    response = auth_client.post(
        "/api/jobs/extract/screenshot", files={"file": ("post.png", b"%PDF-1.4 not an image", "image/png")}
    )
    assert response.status_code == 415


def test_extract_requires_sign_in(client):
    assert client.post("/api/jobs/extract/text", json={"text": "hello"}).status_code == 401


def test_job_title_is_optional(auth_client):
    response = auth_client.post("/api/jobs", json={"company": "Calcey", "description": "QA role", "title": None})
    assert response.status_code == 201
    job = response.json()
    assert job["title"] == ""
    response = auth_client.patch(f"/api/jobs/{job['id']}", json={"title": "QA Engineer"})
    assert response.json()["title"] == "QA Engineer"
    response = auth_client.patch(f"/api/jobs/{job['id']}", json={"title": ""})
    assert response.json()["title"] == ""
    assert auth_client.patch(f"/api/jobs/{job['id']}", json={"company": ""}).status_code == 422
