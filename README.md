# 🤖 AI Resume Matcher

Upload your resume (PDF) and instantly discover the jobs that match you best.
The app parses your resume, scrapes fresh job postings from **Naukri** and
**LinkedIn** with Selenium, and ranks them with an **explainable, weighted
matching score** — never a random percentage.

```
streamlit run app.py
```

## Project structure (deliberately simple)

```
AI_Job_Matching/
├── app.py                 ← Central file: UI + resume parsing + scraping flow
├── matcher.py             ← Matching logic only: similarity, scoring, ranking
├── scraper/
│   ├── naukri.py          ← Selenium scraper for Naukri.com
│   └── linkedin.py        ← Selenium scraper for LinkedIn (guest search)
├── requirements.txt
└── README.md
```

No backend server, no API keys, no database. Everything runs from a single
`streamlit run app.py`.

## Installation

### Mac / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

> ⚠️ On Python 3.13+, some packages (notably `torch`) may not have wheels yet.
> If `pip install` fails, create the venv with a 3.11/3.12 interpreter
> (e.g. `python3.11 -m venv .venv`).

The first match downloads the `all-MiniLM-L6-v2` sentence-transformer model
(~90 MB). If that fails (e.g. no internet), the app **automatically falls back
to TF-IDF-only matching** — set `AI_MATCHER_EMBEDDINGS=off` to skip the model
entirely.

## How to use

1. Upload your resume **PDF** in the sidebar. It is parsed immediately:
   name, email, phone, LinkedIn/GitHub/portfolio, skills (grouped by category),
   projects, experience, education, certifications and total years.
2. Review the **Detected skills** — they also come from your projects and
   experience sections, not just the skills line.
3. Pick your experience, location, role and platforms, then click **Match Jobs**.
4. Search queries are built **dynamically** from your role + top skills.
5. Browse ranked job cards: match %, matched skills, missing skills, the reason
   behind every score, and a one-click **Apply** link. Use the **Filters**
   panel, and export everything as **CSV**.

## How the matching works

Every job gets four component scores, combined with **weights that shift based
on your experience** (freshers lean on skills + projects; seniors lean on
experience):

| Component      | What it measures                                            |
| -------------- | ----------------------------------------------------------- |
| Semantic       | Sentence Transformers + TF-IDF cosine similarity             |
| Skills         | Your skills vs the skills the job actually asks for         |
| Projects       | Your project descriptions vs the job description            |
| Experience     | Your years vs the experience range the job requires         |

The displayed percentage (e.g. **86%**) is the weighted sum — rounded honestly,
never fabricated. Every card explains the score:

- ✔ matched skills (green chips)
- ✖ missing skills (red chips)
- the weight breakdown used for your experience level

Ranking sorts by best score, then **latest posted first**, then strongest skill
match. Ties are broken by recency.

## Scraping notes

- **Naukri** — headless Selenium with stealth arguments, explicit waits,
  retries, deduplication and pagination.
- **LinkedIn** — same Selenium architecture, using LinkedIn's public
  **guest search** endpoint (no login needed). Login walls / CAPTCHAs are
  detected and reported gracefully instead of crashing. Scraping LinkedIn
  against their Terms of Service is done at your own discretion; keep request
  volume low and be polite.
- Both scrapers return the same dictionary shape, so results merge and dedupe
  cleanly across platforms.

## Error handling

| Situation                    | What happens                            |
| ---------------------------- | --------------------------------------- |
| Invalid / non-PDF file       | 📄 Friendly error                        |
| Scanned (image-only) PDF     | 📄 Friendly error                        |
| A platform times out/blocks  | ⚠️ Logged; the other platform still runs |
| No jobs found                | ⚠️ Warning with suggestions              |
| No internet / model missing  | 🌐 Automatic TF-IDF fallback             |
| Anything unexpected          | 🐞 Logged, shown as a friendly alert     |

## Tech stack

Python · Streamlit · Selenium · BeautifulSoup · PyMuPDF · Sentence Transformers ·
scikit-learn (TF-IDF + cosine similarity)

## License

MIT — use it, learn from it, build on it.
