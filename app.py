"""
app.py — Central file of the AI Resume Matcher.

Run with:
    streamlit run app.py

This file owns the whole flow:
    Resume upload → parsing → skill/project/experience extraction
    → dynamic search queries → Naukri + LinkedIn scraping
    → matching (matcher.py) → ranking → premium UI → filters → export

matcher.py contains the scoring logic; scraper/naukri.py and
scraper/linkedin.py contain the Selenium scrapers. Nothing else is needed.
"""

import csv
import hashlib
import html
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pymupdf
import streamlit as st

import matcher
from scraper.naukri import scrape_naukri
from scraper.linkedin import scrape_linkedin

# Supabase persistence — optional; app works without it (no crash if missing)
try:
    import supabase
except ImportError:
    import sys
    import subprocess
    print("[setup] Auto-installing supabase package in the Streamlit environment...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "supabase", "--break-system-packages"])

try:
    import supabase_client as _supa
    _SUPA_ENABLED = True
except Exception:
    _supa = None  # type: ignore
    _SUPA_ENABLED = False

st.set_page_config(
    page_title="AI Resume Matcher",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------
# Premium UI CSS (dark glassmorphism, injected at runtime)
# --------------------------------------------------------------------------
_APP_CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap');
:root{--bg:#f6f8fc;--surface:#fff;--text:#172033;--muted:#667085;--line:#e7ebf2;--primary:#635bff;--primary-soft:#eeecff;--success:#12b76a;--success-soft:#e9f9f1;--warning:#f79009;--danger:#f04438;--danger-soft:#fff0ee;--shadow:0 12px 34px rgba(16,24,40,.07);--shadow-sm:0 4px 16px rgba(16,24,40,.05)}
*,*::before,*::after{box-sizing:border-box}
html,body,[data-testid="stAppViewContainer"],[data-testid="stApp"]{background:var(--bg)!important;color:var(--text)!important;font-family:'DM Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif!important}
[data-testid="stHeader"]{display:none!important;}
[data-testid="stToolbar"],[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"]{display:none!important}
.block-container{width:100%!important;max-width:1380px!important;padding:28px 28px 42px!important}
h1,h2,h3,h4{font-family:'Plus Jakarta Sans','DM Sans',sans-serif!important;color:var(--text)!important;letter-spacing:-.035em!important}
h1{font-size:clamp(2.05rem,3vw,3.1rem)!important;line-height:1.08!important;font-weight:800!important;margin:0!important}
h2{font-size:1.45rem!important;font-weight:800!important}h3{font-size:1.12rem!important;font-weight:750!important}
p,[data-testid="stMarkdownContainer"]{color:var(--text)}.stCaption{color:var(--muted)!important}
[data-testid="stHorizontalBlock"]{align-items:flex-start!important}
.st-key-control-panel{position:sticky!important;top:20px!important;z-index:20;background:rgba(255,255,255,.97)!important;border:1px solid var(--line)!important;border-radius:20px!important;box-shadow:var(--shadow)!important;padding:20px!important;overflow:hidden!important}
.st-key-control-panel [data-testid="stVerticalBlock"]{gap:.35rem!important}
.panel-brand{display:flex;align-items:center;gap:11px;margin-bottom:4px}.brand-mark{width:42px;height:42px;border-radius:13px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#6d63ff,#8a5cf6);color:#fff;font-size:21px;box-shadow:0 8px 18px rgba(99,91,255,.25)}.brand-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:1rem;font-weight:800;letter-spacing:-.02em}.brand-subtitle{color:var(--muted);font-size:.72rem}.panel-copy{color:var(--muted);font-size:.72rem;line-height:1.5;margin:8px 0 14px}
.section-kicker{color:#7b8495;font-size:.67rem;text-transform:uppercase;letter-spacing:.11em;font-weight:800;margin:16px 0 7px}
.resume-status{display:flex;align-items:center;gap:7px;padding:8px 10px;border:1px solid #d9f1e5;background:var(--success-soft);color:#087443;border-radius:10px;font-size:.73rem;font-weight:800;margin:8px 0 10px}
[data-testid="stFileUploader"]{background:linear-gradient(180deg,#fbfcff,#f7f8ff)!important;border:1.5px dashed #bfc6ff!important;border-radius:14px!important;padding:3px!important;transition:.2s ease!important}
[data-testid="stFileUploader"]:hover{border-color:var(--primary)!important;background:#f5f3ff!important;box-shadow:0 0 0 4px rgba(99,91,255,.07)!important}[data-testid="stFileUploader"] section{padding:10px!important}[data-testid="stFileUploader"] button{border-radius:9px!important;border:1px solid #d8ddff!important;background:#fff!important;color:#5148e5!important;font-weight:700!important}
[data-testid="stTextInput"] input,[data-testid="stSelectbox"] div[data-baseweb="select"]>div{min-height:43px!important;border:1px solid #dfe3eb!important;border-radius:10px!important;background:#fff!important;color:var(--text)!important;box-shadow:none!important}
[data-testid="stTextInput"] input:focus{border-color:#9b95ff!important;box-shadow:0 0 0 3px rgba(99,91,255,.10)!important}label{color:#344054!important;font-size:.75rem!important;font-weight:700!important;margin-bottom:3px!important}
.search-divider{height:1px;background:var(--line);margin:16px 0 4px}
.primary-cta button,[data-testid="baseButton-primary"] button{min-height:46px!important;border:0!important;border-radius:11px!important;background:linear-gradient(135deg,#635bff,#7657ef)!important;color:#fff!important;font-weight:800!important;box-shadow:0 9px 20px rgba(99,91,255,.23)!important;transition:.18s ease!important}
.primary-cta button:hover,[data-testid="baseButton-primary"] button:hover{transform:translateY(-1px)!important;box-shadow:0 12px 24px rgba(99,91,255,.3)!important}.secondary-cta button{min-height:41px!important;border-radius:10px!important;border:1px solid var(--line)!important;background:#fff!important;color:#475467!important;font-weight:700!important}
.hero{position:relative;background:radial-gradient(circle at 92% 10%,rgba(118,87,239,.12),transparent 30%),radial-gradient(circle at 70% 90%,rgba(99,91,255,.07),transparent 32%),#fff;border:1px solid var(--line);border-radius:22px;padding:29px 31px 26px;box-shadow:var(--shadow-sm);overflow:hidden;margin-bottom:19px}
.hero:after{content:"";position:absolute;right:-70px;top:-80px;width:210px;height:210px;border-radius:50%;border:1px solid rgba(99,91,255,.1);box-shadow:0 0 0 28px rgba(99,91,255,.025),0 0 0 58px rgba(99,91,255,.018)}
.hero-eyebrow{display:inline-flex;align-items:center;gap:7px;padding:6px 10px;border-radius:999px;background:var(--primary-soft);color:#5148e5;font-size:.68rem;font-weight:800;letter-spacing:.05em;text-transform:uppercase;margin-bottom:12px}
.hero-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:clamp(2rem,3.2vw,3rem);line-height:1.06;letter-spacing:-.05em;font-weight:800;color:var(--text);margin:0}.hero-title span{background:linear-gradient(110deg,#5148e5,#8b5cf6);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}.hero-copy{max-width:760px;color:#667085;font-size:.92rem;line-height:1.65;margin-top:11px}.hero-tags{display:flex;flex-wrap:wrap;gap:7px;margin-top:16px}.hero-tag{display:inline-flex;padding:6px 9px;border:1px solid var(--line);border-radius:8px;background:#fff;color:#667085;font-size:.68rem;font-weight:700}
.analytics-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:1.13rem;font-weight:800;letter-spacing:-.025em;margin:7px 0 11px}.how-card{background:#fff;border:1px solid var(--line);border-radius:17px;padding:19px;box-shadow:var(--shadow-sm);height:100%}.how-number{width:31px;height:31px;border-radius:9px;display:flex;align-items:center;justify-content:center;background:var(--primary-soft);color:#5148e5;font-size:.69rem;font-weight:800;margin-bottom:11px}.how-title{font-weight:800;font-size:.9rem;margin-bottom:5px}.how-copy{color:var(--muted);font-size:.75rem;line-height:1.55}
.candidate-card{background:#fff;border:1px solid var(--line);border-radius:17px;padding:16px 18px;box-shadow:var(--shadow-sm);margin-bottom:17px}.candidate-top{display:flex;align-items:center;justify-content:space-between;gap:12px}.candidate-name{font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:1rem;letter-spacing:-.02em}.candidate-meta{color:var(--muted);font-size:.72rem;margin-top:3px}.candidate-score{padding:7px 9px;border-radius:9px;background:#f5f3ff;color:#5148e5;font-size:.68rem;font-weight:800;white-space:nowrap}.skill-wrap{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}.skill-chip{display:inline-flex;padding:5px 9px;border-radius:999px;background:#f5f7fa;border:1px solid #e8ebf0;color:#475467;font-size:.66rem;font-weight:700}
[data-testid="stMetric"]{background:#fff!important;border:1px solid var(--line)!important;border-radius:15px!important;padding:15px 16px!important;box-shadow:var(--shadow-sm)!important}[data-testid="stMetricLabel"]{color:#667085!important;font-size:.7rem!important;font-weight:700!important}[data-testid="stMetricValue"]{color:var(--text)!important;font-family:'Plus Jakarta Sans',sans-serif!important;font-size:1.38rem!important;font-weight:800!important}.stats-source{display:flex;gap:7px;flex-wrap:wrap;margin:9px 0 17px}.source-pill{padding:5px 8px;border-radius:7px;background:#fff;border:1px solid var(--line);color:#667085;font-size:.65rem;font-weight:700}
.results-toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;margin:17px 0 11px}.results-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:1.22rem;font-weight:800;letter-spacing:-.03em}.results-sub{color:var(--muted);font-size:.73rem;margin-top:2px}[data-testid="stDownloadButton"] button{min-height:40px!important;border-radius:9px!important;border:1px solid var(--line)!important;background:#fff!important;color:#475467!important;font-weight:750!important}
.st-key-job-card-1,.st-key-job-card-2,.st-key-job-card-3,.st-key-job-card-4,.st-key-job-card-5,.st-key-job-card-6,.st-key-job-card-7,.st-key-job-card-8,.st-key-job-card-9,.st-key-job-card-10,.st-key-job-card-11,.st-key-job-card-12,.st-key-job-card-13,.st-key-job-card-14,.st-key-job-card-15,.st-key-job-card-16,.st-key-job-card-17,.st-key-job-card-18,.st-key-job-card-19,.st-key-job-card-20{background:#fff!important;border:1px solid var(--line)!important;border-radius:18px!important;box-shadow:var(--shadow-sm)!important;padding:0!important;margin:0 0 13px!important;transition:.18s ease!important}
.st-key-job-card-1:hover,.st-key-job-card-2:hover,.st-key-job-card-3:hover,.st-key-job-card-4:hover,.st-key-job-card-5:hover,.st-key-job-card-6:hover,.st-key-job-card-7:hover,.st-key-job-card-8:hover,.st-key-job-card-9:hover,.st-key-job-card-10:hover,.st-key-job-card-11:hover,.st-key-job-card-12:hover,.st-key-job-card-13:hover,.st-key-job-card-14:hover,.st-key-job-card-15:hover,.st-key-job-card-16:hover,.st-key-job-card-17:hover,.st-key-job-card-18:hover,.st-key-job-card-19:hover,.st-key-job-card-20:hover{border-color:#d4d0ff!important;box-shadow:0 10px 30px rgba(16,24,40,.08)!important;transform:translateY(-1px)}
.job-card-inner{padding:18px 19px 16px}.job-grid{display:grid;grid-template-columns:70px minmax(0,1fr) 80px;gap:14px;align-items:center}.rank{width:70px;height:70px;border-radius:15px;display:flex;flex-direction:column;align-items:center;justify-content:center;background:linear-gradient(145deg,#f7f6ff,#eeecff);border:1px solid #e4e1ff}.rank-num{color:#5148e5;font-size:.61rem;font-weight:800;text-transform:uppercase;letter-spacing:.08em}.rank-value{color:#3730a3;font-family:'Plus Jakarta Sans',sans-serif;font-size:1.28rem;font-weight:800;line-height:1.05;margin-top:2px}.job-title{font-family:'Plus Jakarta Sans',sans-serif;font-size:.98rem;font-weight:800;line-height:1.28;color:var(--text);margin:0}.job-company{color:#475467;font-size:.76rem;font-weight:700;margin-top:4px}.job-meta-row{display:flex;flex-wrap:wrap;gap:6px 9px;margin-top:8px;color:#667085;font-size:.68rem}.meta-item{display:inline-flex;align-items:center;gap:4px}.badge{display:inline-flex;padding:4px 7px;border-radius:6px;font-size:.58rem;font-weight:800;letter-spacing:.05em;text-transform:uppercase;vertical-align:middle;margin-left:5px}.badge-li{background:#eef7ff;color:#1769aa;border:1px solid #cce7ff}.badge-nk{background:#fff5eb;color:#b54708;border:1px solid #ffe0c2}.mode-badge{display:inline-flex;padding:4px 7px;border-radius:6px;background:#f2f4f7;color:#475467;font-size:.59rem;font-weight:800}.score-box{text-align:right;min-width:80px}.score-ring{width:68px;height:68px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin-left:auto;position:relative;background:conic-gradient(var(--score-color) var(--score),#edf0f5 0)}.score-ring:before{content:"";position:absolute;inset:7px;background:#fff;border-radius:50%}.score-number{position:relative;z-index:1;font-family:'Plus Jakarta Sans',sans-serif;font-size:.84rem;font-weight:800;color:var(--text)}.score-label{font-size:.59rem;font-weight:800;margin-top:5px}.card-divider{height:1px;background:#eef0f4;margin:14px 0 12px}.match-section{margin-bottom:9px}.match-label{font-size:.61rem;text-transform:uppercase;letter-spacing:.08em;font-weight:800;color:#98a2b3;margin-bottom:6px}.chip-row{display:flex;flex-wrap:wrap;gap:5px}.chip-match{display:inline-flex;padding:5px 8px;border-radius:7px;background:var(--success-soft);color:#087443;border:1px solid #cdeedc;font-size:.64rem;font-weight:700}.chip-miss{display:inline-flex;padding:5px 8px;border-radius:7px;background:var(--danger-soft);color:#b42318;border:1px solid #ffd2ce;font-size:.64rem;font-weight:700}.description-box{color:#667085;font-size:.74rem;line-height:1.65;background:#f8fafc;border:1px solid #edf0f4;border-radius:10px;padding:10px 11px;margin-top:8px}
[data-testid="stExpander"]{border:1px solid #edf0f4!important;border-radius:10px!important;background:#fafbfc!important;box-shadow:none!important}[data-testid="stExpander"] summary{color:#475467!important;font-size:.7rem!important;font-weight:750!important}.apply-row{margin-top:12px}[data-testid="stLinkButton"] a{min-height:42px!important;border-radius:10px!important;border:1px solid #d9d6ff!important;background:#f7f6ff!important;color:#5148e5!important;font-weight:800!important;transition:.18s ease!important}[data-testid="stLinkButton"] a:hover{background:#eeecff!important;border-color:#c9c4ff!important}
.loader-text{color:#475467;font-size:.77rem;font-weight:700}[data-testid="stProgress"]{height:8px!important;margin:10px 0 5px!important}[data-testid="stProgress"]>div{background:#ececf8!important;border-radius:999px!important}[data-testid="stProgress"]>div>div{background:linear-gradient(90deg,#635bff,#8b5cf6)!important;border-radius:999px!important}[data-testid="stAlert"]{border-radius:12px!important;border:1px solid var(--line)!important}.footer{text-align:center;color:#98a2b3;font-size:.67rem;padding:27px 0 3px}
@media (max-width:900px){.block-container{padding:18px 14px 30px!important}.st-key-control-panel{position:relative!important;top:auto!important}.hero{padding:23px 21px}.job-grid{grid-template-columns:58px minmax(0,1fr)}.score-box{grid-column:2;text-align:left}.score-ring{margin-left:0}.rank{width:58px;height:58px}.rank-value{font-size:1.08rem}}
</style>"""


# --------------------------------------------------------------------------
# Skill recognition vocabulary (used to *recognize* skills in text).
# Everything else — scores, filters, queries — is computed dynamically.
# --------------------------------------------------------------------------
SKILL_TAXONOMY = {
    "Languages": [
        "python", "javascript", "typescript", "java", "c++", "c#", "c", "go", "golang",
        "rust", "swift", "kotlin", "ruby", "php", "scala", "r", "matlab", "sql",
        "bash", "shell", "powershell", "dart", "perl", "haskell", "solidity",
        "groovy", "objective-c", "visual basic", "html", "css",
    ],
    "Frameworks": [
        "django", "flask", "fastapi", "react", "react.js", "reactjs", "angular",
        "angularjs", "vue", "vue.js", "node", "node.js", "express", "express.js",
        "next.js", "nuxt.js", "svelte", "spring", "spring boot", "hibernate",
        "rails", "laravel", "asp.net", ".net", "flutter", "react native",
        "tensorflow", "pytorch", "keras", "scikit-learn", "pandas", "numpy",
        "pyspark", "spark", "hadoop", "hive", "kafka", "airflow", "dbt",
        "streamlit", "gradio", "bootstrap", "tailwind", "tailwind css", "jquery",
        "redux", "graphql", "apollo", "grpc", "rabbitmq", "celery", "symfony",
        "codeigniter", "opencv", "nltk", "spacy", "transformers", "langchain",
        "llamaindex", "paddlepaddle", "mxnet", "jax", "shiny", "d3", "three.js",
        "socket.io", "chart.js", "electron", "tensorflow lite",
    ],
    "Libraries": [
        "requests", "beautifulsoup", "selenium", "playwright", "puppeteer",
        "cheerio", "matplotlib", "seaborn", "plotly", "scipy", "pillow",
        "gunicorn", "uvicorn", "pydantic", "sqlalchemy", "alembic", "psycopg2",
        "pymongo", "redis-py", "boto3", "click", "typer", "pytest", "unittest",
        "axios", "lodash", "moment", "zod", "prisma", "typeorm", "sequelize",
        "mongoose", "redux toolkit", "react query", "react hook form", "framer motion",
    ],
    "Databases": [
        "mysql", "postgresql", "postgres", "mongodb", "sqlite", "oracle",
        "sql server", "mssql", "db2", "redis", "cassandra", "dynamodb", "couchdb",
        "neo4j", "elasticsearch", "opensearch", "influxdb", "firebase",
        "firestore", "supabase", "mariadb", "clickhouse", "bigquery", "redshift",
        "snowflake", "hbase", "memcached", "amazon s3",
    ],
    "Cloud & DevOps": [
        "aws", "amazon web services", "ec2", "s3", "lambda", "rds", "cloudfront",
        "ecs", "eks", "fargate", "azure", "microsoft azure", "gcp", "google cloud",
        "kubernetes", "k8s", "docker", "terraform", "ansible", "puppet", "chef",
        "jenkins", "github actions", "gitlab ci", "circleci", "travis", "argo",
        "helm", "prometheus", "grafana", "datadog", "new relic", "cloudwatch",
        "nginx", "apache", "linux", "ubuntu", "centos", "red hat", "windows",
        "macos", "unix", "ci/cd", "devops", "microservices", "serverless",
        "load balancer", "vpc", "iam", "docker compose", "kubectl", "azure devops",
    ],
    "AI & ML": [
        "machine learning", "deep learning", "ai", "artificial intelligence",
        "nlp", "natural language processing", "computer vision", "llm",
        "large language models", "rag", "generative ai", "genai", "chatgpt",
        "openai", "gpt", "claude", "gemini", "mlops", "data science",
        "data analysis", "statistics", "regression", "classification",
        "clustering", "neural networks", "cnn", "rnn", "lstm", "transformers",
        "fine-tuning", "prompt engineering", "vector database", "embeddings",
        "recommendation systems", "anomaly detection", "time series",
        "feature engineering", "model deployment", "a/b testing", "pandas",
        "data visualization", "etl", "data engineering", "big data",
    ],
    "Testing": [
        "selenium", "pytest", "unittest", "junit", "jest", "mocha", "chai",
        "cypress", "testng", "cucumber", "karate", "postman", "rest assured",
        "soapui", "jmeter", "gatling", "k6", "mockito", "tdd", "test driven development",
        "bdd", "unit testing", "integration testing", "e2e testing",
        "automation testing", "manual testing", "quality assurance", "qa",
        "playwright", "regression testing", "load testing", "api testing",
    ],
    "Version Control": [
        "git", "github", "gitlab", "bitbucket", "svn", "mercurial",
    ],
    "Tools": [
        "jira", "confluence", "slack", "trello", "asana", "notion", "figma",
        "zeplin", "sketch", "photoshop", "illustrator", "postman", "insomnia",
        "swagger", "openapi", "vscode", "visual studio", "intellij", "pycharm",
        "eclipse", "sublime", "vim", "npm", "yarn", "pnpm", "webpack", "vite",
        "babel", "eslint", "prettier", "gradle", "maven", "make", "cmake",
        "excel", "power bi", "tableau", "looker", "metabase", "kibana", "splunk",
        "git bash", "terminal", "docker desktop",
    ],
    "Soft Skills": [
        "leadership", "communication", "teamwork", "collaboration",
        "problem solving", "critical thinking", "time management",
        "adaptability", "creativity", "mentoring", "presentation",
        "public speaking", "agile", "scrum", "kanban", "stakeholder management",
        "project management", "negotiation", "conflict resolution",
        "decision making", "ownership", "initiative", "attention to detail",
        "customer service", "documentation", "cross-functional", "remote work",
        "self-motivated", "fast learner", "multitasking", "prioritization",
    ],
}

# Common filler words that should never be treated as skills.
_FILLER_WORDS = {
    "the", "and", "with", "for", "from", "your", "our", "this", "that",
    "using", "used", "using the", "use of", "application", "various",
    "multiple", "including", "tools", "technologies", "technology", "software",
}


def _is_present(text: str, term: str) -> bool:
    """Word-boundary check for a skill term inside lowercased text."""
    pattern = rf"(?<![a-z0-9+#.]){re.escape(term)}(?![a-z0-9+#.])"
    return re.search(pattern, text) is not None


def _valid_skill_candidate(candidate: str) -> bool:
    candidate = candidate.strip()
    if not (2 <= len(candidate) <= 40):
        return False
    if not re.search(r"[a-z]", candidate):
        return False
    if candidate in _FILLER_WORDS or re.match(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*\d{4}$", candidate, re.I):
        return False
    return True


# --------------------------------------------------------------------------
# Resume text extraction (PDF)
# --------------------------------------------------------------------------
def extract_resume_text(pdf_bytes: bytes) -> str:
    """Extract and clean all text from a PDF resume.

    Line breaks are preserved because section detection (SKILLS, PROJECTS,
    EXPERIENCE, ...) relies on them. If the PDF lost its line structure,
    line breaks are re-inserted before known section headers.
    """
    if not pdf_bytes:
        raise ValueError("The uploaded file is empty.")
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # not a valid PDF
        raise ValueError("Could not read the file. Please upload a valid PDF resume.") from exc

    if document.page_count == 0:
        raise ValueError("The PDF contains no pages.")

    pages = [page.get_text() for page in document]
    document.close()

    # Keep one line per extracted line (normalize only horizontal whitespace).
    lines = [re.sub(r"[ \t]+", " ", raw).strip() for raw in "\n".join(pages).splitlines()]
    text = "\n".join(lines)

    # Defensive fallback: some PDFs return flowing text with no line breaks.
    # Re-insert breaks before known section headers so detection still works.
    # Only capitalized headers are split ("SKILLS", "Work Experience") — a
    # lowercase "experience" inside a sentence is never a section header.
    if text.count("\n") < 5:
        joined = re.sub(r"\s+", " ", text)
        header_pattern = "|".join(re.escape(h) for patterns in _SECTION_HEADERS.values() for h in patterns)
        parts = []
        cursor = 0
        for match in re.finditer(rf"\b({header_pattern})\b", joined):
            if match.group(0)[0].isupper():
                parts.append(joined[cursor:match.start()])
                cursor = match.start()
        parts.append(joined[cursor:])
        text = "\n".join(p.strip() for p in parts if p.strip())

    if not text.strip():
        raise ValueError(
            "No readable text was found in the PDF. "
            "Scanned/image-only PDFs are not supported yet."
        )
    return text


# --------------------------------------------------------------------------
# Contact & identity extraction
# --------------------------------------------------------------------------
def extract_contact(text: str) -> dict:
    """Extract email, phone, LinkedIn, GitHub and portfolio links."""
    email = ""
    match = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
    if match:
        email = match.group(0).strip(".")

    phone = ""
    for candidate in re.findall(r"(\+?\d[\d\s\-\.\(\)]{8,17}\d)", text):
        digits = re.sub(r"\D", "", candidate)
        if 10 <= len(digits) <= 15 and not re.fullmatch(r"(?:19|20)\d{2}", digits):
            phone = candidate.strip()
            break

    linkedin = ""
    match = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[^\s\)\]>,]+", text, re.IGNORECASE)
    if match:
        linkedin = match.group(0).rstrip(".,")

    github = ""
    match = re.search(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_.\-]+", text)
    if match:
        github = match.group(0).rstrip(".,")

    portfolio = ""
    for url in re.findall(r"https?://[^\s\)\]>,]+", text):
        url = url.rstrip(".,")
        if "linkedin.com" in url or "github.com" in url:
            continue
        portfolio = url
        break

    return {"email": email, "phone": phone, "linkedin": linkedin, "github": github, "portfolio": portfolio}


def _is_section_header(line: str) -> bool:
    normalized = re.sub(r"[^a-z\s]", "", line.lower()).strip()
    headers = {
        "skills", "technical skills", "experience", "work experience",
        "projects", "education", "certifications", "summary", "objective",
        "internships", "profile", "languages", "achievements", "personal details",
    }
    return normalized in headers


def detect_name(text: str, email: str) -> str:
    """Best-effort name detection from the first lines of the resume."""
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 60 or re.search(r"[@/]|^\d", line):
            continue
        if sum(c.isupper() for c in line) < 2 or _is_section_header(line):
            continue
        if not re.search(r"[a-zA-Z]{3,}", line):
            continue
        return line
    if email:
        return email.split("@")[0].replace(".", " ").replace("_", " ").title()
    return ""


# --------------------------------------------------------------------------
# Section detection
# --------------------------------------------------------------------------
_SECTION_HEADERS = {
    "skills": ["technical skills", "skills", "core competencies", "skill set", "areas of expertise"],
    "projects": ["projects", "project experience", "academic projects", "personal projects", "professional projects", "key projects", "selected projects"],
    "experience": ["work experience", "professional experience", "employment history", "work history", "experience", "career history"],
    "internships": ["internships", "internship experience", "internship", "industrial training"],
    "education": ["education", "academic background", "academic qualifications", "educational qualifications", "academics"],
    "certifications": ["certifications", "certificates", "licenses", "courses", "professional certifications", "certification"],
    "summary": ["professional summary", "summary", "profile", "objective", "career objective", "about me", "overview"],
    "achievements": ["achievements", "awards", "honors"],
}


def _normalize_header(line: str) -> str:
    return re.sub(r"[^a-z\s]", "", line.lower()).strip()


def detect_sections(text: str) -> dict:
    """Split the resume into canonical sections → list of raw lines."""
    sections: dict[str, list] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        normalized = _normalize_header(line)
        matched = None
        for section, patterns in _SECTION_HEADERS.items():
            if normalized in patterns or any(
                len(normalized) <= 22 and normalized.startswith(p) for p in patterns if len(p) >= 4
            ):
                matched = section
                break
        if matched:
            current = matched
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
    return sections


# --------------------------------------------------------------------------
# Skill extraction (skills section + projects + experience, merged)
# --------------------------------------------------------------------------
def extract_skills(text: str):
    """Find skills in text. Returns (ordered_unique_skills, skill→category)."""
    low = text.lower()
    found: list[str] = []
    category_of: dict[str, str] = {}

    # 1) Taxonomy scan — the recognition vocabulary above
    for category, terms in SKILL_TAXONOMY.items():
        for term in terms:
            if _is_present(low, term) and term not in category_of:
                category_of[term] = category
                found.append(term)

    # 2) Dynamic capture — phrases like "proficient in X, Y" that may list
    #    skills not present in the taxonomy at all.
    for phrase in re.findall(
        r"(?:proficient in|experienced with|experience with|expertise in|"
        r"knowledge of|working knowledge of|hands-on with|familiar with|"
        r"good knowledge of|skills?[:;])\s+([^\n.]+)",
        low,
    ):
        for candidate in re.split(r"[,;/|&]+|\band\b|\bwith\b", phrase):
            candidate = re.sub(r"^[\d\-\s]+", "", candidate).strip(" .()")
            if _valid_skill_candidate(candidate) and candidate not in category_of:
                category_of[candidate] = "Other"
                found.append(candidate)

    return found, category_of


def extract_all_resume_skills(text: str, sections: dict) -> list:
    """Merge skills from all resume sections, ordered by signal reliability.

    Order: skills section (explicitly claimed) → projects (actually built with)
    → experience + internships (proven at work) → certifications → summary
    → full text catch-all.  Deduped: first occurrence wins so the ordering
    is preserved and the most reliable signal surfaces first.
    """
    merged: list[str] = []
    seen: set = set()

    def _scan(blob: str) -> None:
        skills, _ = extract_skills(blob)
        for s in skills:
            if s not in seen:
                seen.add(s)
                merged.append(s)

    # Scan in priority order — each section joined into one blob for efficiency
    for key in ("skills", "projects", "experience", "internships", "certifications", "summary", "achievements"):
        lines = sections.get(key, [])
        if lines:
            _scan(" ".join(str(l) for l in lines if l))

    _scan(text)  # catch-all: picks up anything the section scan missed
    return merged


# --------------------------------------------------------------------------
# Projects, experience, education, certifications
# --------------------------------------------------------------------------
def _clean_bullet(line: str) -> str:
    return re.sub(r"^[\s\-•*·–—>\d.)]+", "", line).strip()


def _is_bullet(line: str) -> bool:
    return bool(re.match(r"^[\s\-•*·–—>\d.)]", line)) and not re.match(r"^\d{4}", line)


def _split_name_desc(line: str):
    """Split a 'Name ·/—/–/ - Description' line into (name, description).

    Plain hyphens are only used as a last resort because they are common
    inside names (e.g. 'E-Commerce', '2023 - 2024').
    """
    for separator in (" · ", " — ", " – "):
        if separator in line:
            head, _, tail = line.partition(separator)
            if tail.strip() and len(tail.strip()) > 20:
                return head.strip(), tail.strip()
    match = re.search(r"(?<!\d)\s+-\s+(?!\d)", line)
    if match:
        head, tail = line[:match.start()].strip(), line[match.end():].strip()
        if tail and len(tail) > 20:
            return head, tail
    return None


def _split_blocks(lines: list) -> list:
    """Group consecutive lines into blocks; a short heading starts a new block."""
    blocks = []
    current = []
    for raw in lines:
        line = raw.strip()
        if not line:
            if current:
                blocks.append(current)
                current = []
            continue
        is_heading = (
            len(line) <= 70
            and not line.rstrip().endswith((".", ","))
            and not _is_bullet(line)
            and not _is_section_header(line)
        )
        if current and is_heading and not re.match(r"^[a-z]", line):
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _find_duration(text: str) -> str:
    """Best-effort extraction of a date range / duration string.

    Handles '—', '–', '-' and '·' as separators (some PDFs render dashes
    as a middle dot), as well as the '2022 to Present' form.
    """
    patterns = [
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*\d{4}\s*(?:[·–—-]|to)\s*(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*)?(?:\d{4}|present|current|now|ongoing|till date)",
        r"\b(?:19|20)\d{2}\s*(?:[·–—-]|to)\s*(?:(?:19|20)\d{2}|present|current|now|ongoing|till date)",
        r"\b\d+\s*(?:\.\d+)?\s*\+\s*(?:years|yrs)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return ""


def _span_years(duration: str) -> float:
    """Convert a duration string like '2020 - 2024' into years."""
    if not duration:
        return 0.0
    years_found = [int(y) for y in re.findall(r"(?:19|20)\d{2}", duration)]
    if not years_found:
        return 0.0
    start = min(years_found)
    end = max(years_found)
    if re.search(r"present|current|now|ongoing|till date", duration, re.IGNORECASE) and len(years_found) == 1:
        end = datetime.now().year
    if end < start:
        return 0.0
    return end - start


def extract_projects(section_lines: list) -> list:
    """Parse the projects section into project dicts (line-scan, best-effort)."""
    projects = []
    current = None  # dict: name / desc_lines / duration

    def flush():
        nonlocal current
        if current and current["name"]:
            description = " ".join(current["desc_lines"])
            projects.append({
                "name": current["name"],
                "description": description,
                "technologies": extract_skills(description)[0][:12] if description else [],
                "duration": current["duration"],
                "role": "",
            })
        current = None

    for raw in section_lines:
        line = _clean_bullet(raw)
        if not line:
            continue
        # "Name ·/— Description..." style one-liner starts a new project
        split = _split_name_desc(line)
        if split:
            flush()
            name, description = split
            name = re.sub(r"\([^)]*\d{4}[^)]*\)", "", name).strip(" -–—·")
            current = {
                "name": name[:80],
                "desc_lines": [description],
                "duration": _find_duration(line),
            }
        elif _is_bullet(line):
            if current is None:
                current = {"name": "", "desc_lines": [], "duration": ""}
            current["desc_lines"].append(line)
        elif len(line) <= 70 and not line.rstrip().endswith((".", ",")):
            flush()  # short heading line → new project name
            name = re.sub(r"\([^)]*\d{4}[^)]*\)", "", line).strip(" -–—·")
            current = {"name": name[:80], "desc_lines": [], "duration": _find_duration(line)}
        else:
            if current is None:
                current = {"name": "", "desc_lines": [], "duration": ""}
            current["desc_lines"].append(line)

    flush()
    return projects


def extract_experience(section_lines: list) -> list:
    """Parse work-experience lines into role/company/duration/description dicts."""
    entries = []
    company_suffixes = ("pvt", "ltd", "llc", "inc", "corp", "corporation", "technologies",
                        "tech", "solutions", "labs", "systems", "services", "consulting",
                        "limited", "works", "digital", "ventures", "studios")
    city_hints = {"bengaluru", "bangalore", "mumbai", "delhi", "new delhi", "ncr", "hyderabad",
                  "pune", "chennai", "kolkata", "gurgaon", "gurugram", "noida", "ahmedabad",
                  "remote", "india"}

    def split_role_company(role_part: str):
        """Heuristic: 'Role, Company, City' → (role, company)."""
        parts = [p.strip() for p in role_part.split(",") if p.strip()]
        while parts and parts[-1].strip().lower() in city_hints:
            parts.pop()
        company = ""
        for index in range(len(parts) - 1, -1, -1):
            if parts[index].lower().rstrip(".").endswith(company_suffixes):
                company = parts.pop(index)
                break
        return ", ".join(parts).strip(), company

    for block in _split_blocks(section_lines):
        if not block:
            continue
        role, company = "", ""
        duration = _find_duration(" ".join(block))
        for line in block:
            clean = _clean_bullet(line)
            # "Role at Company"
            match = re.search(r"^(.+?)\s+at\s+(.+)$", clean)
            if match:
                role, company = match.group(1).strip(), match.group(2).strip()
                break
            # "Role, Company ·/— dates" (hyphen avoided — too common in text)
            match = re.search(r"^(.+?)\s*[·–—]\s*(.+)$", clean)
            if match and re.search(r"\d{4}", match.group(2)):
                role, company = split_role_company(match.group(1))
                break
            # Fallback for "Role, Company - 2020 to 2022" style lines
            match = re.search(r"^(.+?)\s*-\s*(.+)$", clean)
            if match and re.search(r"\d{4}", match.group(2)) and "," in match.group(1):
                role, company = split_role_company(match.group(1))
                break
        if role or company:
            description = " ".join(_clean_bullet(l) for l in block if _is_bullet(l))
            entries.append({"role": role, "company": company, "duration": duration, "description": description})
    return entries


def extract_education(section_lines: list) -> list:
    """Education lines, cleaned."""
    entries = []
    for raw in section_lines:
        line = _clean_bullet(raw)
        if line and len(line) < 160 and re.search(r"[A-Za-z]{3,}", line):
            entries.append(line)
    return entries


def extract_certifications(section_lines: list) -> list:
    """Certification lines, cleaned."""
    entries = []
    for raw in section_lines:
        line = _clean_bullet(raw)
        if line and len(line) < 140 and not _is_section_header(line):
            entries.append(line)
    return entries


def extract_years_experience(text: str, durations: list) -> float:
    """Total years of experience = max of explicit years and date ranges."""
    years = 0.0
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years|yrs)", text, re.IGNORECASE):
        years = max(years, float(match.group(1)))
    for duration in durations:
        years = max(years, _span_years(duration))
    return round(years, 1)


# --------------------------------------------------------------------------
# Resume parsing → candidate profile
# --------------------------------------------------------------------------
def parse_resume(pdf_bytes: bytes) -> dict:
    """Full pipeline from PDF bytes to a structured candidate profile."""
    text = extract_resume_text(pdf_bytes)
    sections = detect_sections(text)
    contact = extract_contact(text)
    skills = extract_all_resume_skills(text, sections)
    skills, category_of = extract_skills(" ".join(skills)) if skills else ([], {})

    projects = extract_projects(sections.get("projects", []))
    experience = extract_experience(sections.get("experience", []))
    internships = extract_experience(sections.get("internships", []))
    education = extract_education(sections.get("education", []))
    certifications = extract_certifications(sections.get("certifications", []))
    summary = sections.get("summary", [])

    # Only work/internship spans count as experience — never education or
    # project durations, or a fresher's "years" would be inflated.
    durations = [e.get("duration", "") for e in experience + internships]
    years = extract_years_experience(text, durations)

    return {
        "name": detect_name(text, contact["email"]),
        "email": contact["email"],
        "phone": contact["phone"],
        "linkedin": contact["linkedin"],
        "github": contact["github"],
        "portfolio": contact["portfolio"],
        "summary": summary,
        "skills": skills,
        "skills_by_category": category_of,
        "projects": projects,
        "experience": experience,
        "internships": internships,
        "education": education,
        "certifications": certifications,
        "years_experience": years,
        "resume_text": text,
    }


# --------------------------------------------------------------------------
# Search orchestration
# --------------------------------------------------------------------------
def _infer_role(profile: dict) -> str:
    """Infer a job title when the user provides no preferred role.

    Tries work/internship titles first, then derives from the dominant
    skill category so the search is never just a bare skill keyword.
    """
    _DATE_RE = re.compile(
        r"\b(\d{1,2}\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b"
        r"|\b\d{4}\b|\bpresent\b|\bcurrent\b",
        re.I,
    )
    for exp in profile.get("experience", []) + profile.get("internships", []):
        role = (exp.get("role") or "").strip()
        # Skip entries that are dates, month names or resume artefacts
        if role and 3 < len(role) <= 60 and not _DATE_RE.search(role):
            return role
    cats = profile.get("skills_by_category", {})
    skills = profile.get("skills", [])
    if not skills:
        return "Software Developer"
    cat = cats.get(skills[0], "")
    if cat == "AI & ML":
        return "Machine Learning Engineer"
    if cat == "Cloud & DevOps":
        return "DevOps Engineer"
    if cat == "Frameworks" and skills[0] in ("react", "reactjs", "react.js", "vue", "angular"):
        return "Frontend Developer"
    if cat == "Frameworks" and skills[0] in ("django", "flask", "fastapi", "spring", "spring boot"):
        return "Backend Developer"
    return f"{skills[0].title()} Developer"


def build_search_queries(profile: dict, preferred_role: str, job_type: str, count: int) -> list:
    """Build role-anchored queries so every search is specific.

    Every query is '{role} {skill}' — never a bare skill keyword.
    This stops 'python' from matching data-entry or finance jobs.
    """
    role = (preferred_role or "").strip() or _infer_role(profile)
    queries: list[str] = [role]  # always include the pure role as first query

    for skill in profile.get("skills", []):
        if len(queries) >= count:
            break
        combined = f"{role} {skill}".strip()
        if combined not in queries:
            queries.append(combined)

    suffix = (
        " internship" if job_type == "Internship"
        else " part time" if job_type == "Part-time"
        else ""
    )
    return [f"{q}{suffix}" for q in queries]


# --------------------------------------------------------------------------
def _cached_scrape(query: str, location: str, platform: str,
                   max_pages: int, max_jobs: int) -> list:
    """Scrape jobs directly without local caching."""
    try:
        if platform == "Naukri":
            jobs = scrape_naukri(keyword=query, location=location,
                                 max_pages=max_pages, max_jobs=max_jobs)
        else:
            jobs = scrape_linkedin(keyword=query, location=location,
                                   max_pages=max_pages, max_jobs=max_jobs)
        return jobs
    except Exception as exc:
        print(f"[scrape] live scrape failed {platform} '{query}': {exc}")
        return []


def search_jobs(queries, location, platforms, max_pages, jobs_per_query, progress_cb=None):
    """Scrape all platform × query combos in parallel; merge and dedupe.

    Uses ThreadPoolExecutor so Naukri + LinkedIn run simultaneously.
    """
    combos = [(q, p) for q in queries for p in platforms]
    total = len(combos)
    all_jobs: list = []
    seen: set = set()
    done_count = 0

    def _scrape_one(combo):
        q, plat = combo
        return _cached_scrape(q, location, plat, max_pages, jobs_per_query)

    with ThreadPoolExecutor(max_workers=min(4, max(1, total))) as pool:
        future_to_combo = {pool.submit(_scrape_one, c): c for c in combos}
        for future in as_completed(future_to_combo):
            done_count += 1
            q, plat = future_to_combo[future]
            if progress_cb:
                progress_cb(done_count, total, f"Searched {plat} for \u2018{q}\u2019…")
            try:
                raw = future.result()
            except Exception as exc:
                print(f"[app] thread error {plat} '{q}': {exc}")
                raw = []
            for job in raw:
                title = (job.get("title") or "").strip()
                key = (title.lower(), (job.get("company") or "").strip().lower())
                if not title or key in seen:
                    continue
                seen.add(key)
                job["job_title"] = title
                all_jobs.append(job)
    return all_jobs


def work_mode(job: dict) -> str:
    """Dynamic Remote / Hybrid / Onsite classification from the job text."""
    blob = f"{job.get('location', '')} {job.get('description', '')}".lower()
    if "hybrid" in blob:
        return "Hybrid"
    if "remote" in blob or "work from home" in blob or " wfh" in blob:
        return "Remote"
    return "Onsite"


def enrich_jobs(jobs: list, candidate_skills: list) -> list:
    """Add job_skills, work_mode and experience fields needed by the UI/matcher."""
    for job in jobs:
        blob = f"{job.get('job_title', '')} {job.get('description', '')}"
        job["job_skills"], _ = extract_skills(blob)
        job["job_skills"] = job["job_skills"][:15]
        job["work_mode"] = work_mode(job)
        if not job.get("experience"):
            job["experience"] = None
    return jobs


# --------------------------------------------------------------------------
# Resume metrics (dynamic, never hardcoded)
# --------------------------------------------------------------------------
def resume_metrics(profile: dict):
    checks = {
        "Contact info": bool(profile.get("email") or profile.get("phone") or profile.get("linkedin")),
        "Skills": bool(profile.get("skills")),
        "Projects": bool(profile.get("projects")),
        "Experience": bool(profile.get("experience") or profile.get("years_experience")),
        "Education": bool(profile.get("education")),
        "Certifications": bool(profile.get("certifications")),
    }
    strength = int(round(100 * sum(checks.values()) / len(checks)))
    ats = "High" if strength >= 80 else "Medium" if strength >= 50 else "Low"
    missing = [k for k, ok in checks.items() if not ok]
    return strength, ats, missing


# --------------------------------------------------------------------------
# UI helpers
# --------------------------------------------------------------------------
def _company_name(job: dict) -> str:
    return str(job.get("company") or "Unknown Company")


def _job_location(job: dict) -> str:
    location = str(job.get("location") or "").strip()
    return location if location else ("Remote" if job.get("work_mode") == "Remote" else "Location not disclosed")


def _apply_url(job: dict) -> str:
    value = job.get("url") or ""
    return str(value) if str(value).startswith(("http://", "https://")) else ""


def _pct_color(percentage: int) -> str:
    if percentage >= 75:
        return "#34d399"
    if percentage >= 50:
        return "#fbbf24"
    return "#fb7185"


def _esc(value) -> str:
    return html.escape(str(value or ""))



def _render_resume_summary(profile: dict) -> None:
    name = _esc(profile.get("name") or "Resume profile")
    years = profile.get("years_experience", 0)
    skills = list(profile.get("skills") or [])
    contact_bits = []
    if profile.get("email"): contact_bits.append("Email")
    if profile.get("phone"): contact_bits.append("Phone")
    if profile.get("linkedin"): contact_bits.append("LinkedIn")
    if profile.get("github"): contact_bits.append("GitHub")
    strength, ats, _ = resume_metrics(profile)

    chips = "".join(f'<span class="skill-chip">{_esc(s)}</span>' for s in skills[:12])
    if len(skills) > 12:
        chips += f'<span class="skill-chip">+{len(skills)-12} more</span>'

    st.markdown(
        f'''
        <div class="candidate-card">
          <div class="candidate-top">
            <div>
              <div class="candidate-name">{name}</div>
              <div class="candidate-meta">{years:g} yrs experience{" · " + " · ".join(contact_bits) if contact_bits else ""}</div>
            </div>
            <div class="candidate-score">ATS {strength}% · {ats}</div>
          </div>
          <div class="skill-wrap">{chips if chips else '<span class="skill-chip">No skills detected</span>'}</div>
        </div>
        ''',
        unsafe_allow_html=True,
    )


def render_sidebar():
    st.markdown(
        '''
        <div class="panel-brand">
          <div class="brand-mark">✦</div>
          <div>
            <div class="brand-title">AI Resume Matcher</div>
            <div class="brand-subtitle">Intelligent job discovery</div>
          </div>
        </div>
        <div class="panel-copy">Turn your resume into a targeted search across Naukri and LinkedIn.</div>
        ''',
        unsafe_allow_html=True,
    )

    uploaded = st.file_uploader(
        "Upload resume (PDF)",
        type=["pdf"],
        help="Upload a text-based PDF resume. Maximum size: 200 MB.",
    )

    profile = st.session_state.get("profile")
    if uploaded is not None:
        file_id = getattr(uploaded, "file_id", None) or uploaded.name
        if st.session_state.get("profile_file_id") != file_id:
            try:
                with st.spinner("Parsing resume…"):
                    pdf_bytes = uploaded.getvalue()
                    file_hash = hashlib.sha256(pdf_bytes).hexdigest()
                    cached = _supa.get_cached_resume(file_hash) if _SUPA_ENABLED else None
                    if cached:
                        profile = cached
                        st.toast("Resume loaded from cache", icon="⚡")
                    else:
                        profile = parse_resume(pdf_bytes)
                        if _SUPA_ENABLED:
                            try:
                                _supa.save_resume(file_hash, profile, pdf_bytes)
                            except Exception as _exc:
                                print(f"[supabase] save_resume: {_exc}")
                st.session_state["profile"] = profile
                st.session_state["profile_file_id"] = file_id
            except Exception as e:
                st.error(f"Error parsing resume: {e}")
                profile = None

    if profile:
        st.markdown('<div class="resume-status">✓ Resume parsed successfully</div>', unsafe_allow_html=True)
        with st.expander("View detected skills", expanded=False):
            skills = profile.get("skills") or []
            if skills:
                st.markdown(
                    '<div class="skill-wrap">' +
                    "".join(f'<span class="skill-chip">{_esc(s)}</span>' for s in skills[:24]) +
                    '</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.info("No skills detected.")

    st.markdown('<div class="search-divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-kicker">Search targets</div>', unsafe_allow_html=True)

    preferred_role = st.text_input("Role", placeholder="e.g. Data Scientist")
    experience_band = st.selectbox(
        "Experience",
        ["Fresher (0 yrs)", "1-2 yrs", "3-5 yrs", "5+ yrs"],
    )
    band_years = {"Fresher (0 yrs)": 0, "1-2 yrs": 2, "3-5 yrs": 5, "5+ yrs": 7}

    c1, c2 = st.columns(2, gap="small")
    with c1:
        country = st.selectbox(
            "Country",
            ["India", "USA", "UK", "Canada", "Australia", "UAE", "Singapore",
             "Germany", "France", "Japan", "South Korea"],
        )
    with c2:
        job_type = st.selectbox("Job Type", ["Full-time", "Part-time", "Internship"])

    location = country
    if country == "India":
        state = st.selectbox(
            "State / Region",
            ["All India", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar",
             "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh",
             "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra",
             "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
             "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
             "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi NCR", "Remote"],
        )
        location = state if state != "All India" else "India"

    query_count = 2
    max_pages = 2
    top_n = 20

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
    st.markdown('<div class="primary-cta">', unsafe_allow_html=True)
    submitted = st.button("✦  Match my jobs", type="primary", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    if st.session_state.get("matches"):
        st.markdown('<div style="height:2px"></div>', unsafe_allow_html=True)
        st.markdown('<div class="secondary-cta">', unsafe_allow_html=True)
        if st.button("↺  Start a new search", use_container_width=True):
            for key in ("matches", "stats", "profile", "profile_file_id"):
                st.session_state.pop(key, None)
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(
        '<div style="text-align:center;color:#98a2b3;font-size:.63rem;margin-top:12px">Live sources · Explainable scoring</div>',
        unsafe_allow_html=True,
    )

    return {
        "profile": profile,
        "uploaded": uploaded,
        "preferred_role": preferred_role,
        "location": location,
        "job_type": job_type,
        "platforms": ["Naukri", "LinkedIn"],
        "query_count": query_count,
        "max_pages": max_pages,
        "top_n": top_n,
        "years": band_years[experience_band],
        "submitted": submitted,
    }


def render_header() -> None:
    st.markdown(
        '''
        <div class="hero">
          <div class="hero-eyebrow">✦ AI-powered job matching</div>
          <div class="hero-title">Find jobs that <span>actually fit.</span></div>
          <div class="hero-copy">
            Upload your resume and get ranked opportunities from Naukri and LinkedIn,
            scored against your real skills, projects, experience, and role — not generic keywords.
          </div>
          <div class="hero-tags">
            <span class="hero-tag">Naukri + LinkedIn</span>
            <span class="hero-tag">Live scraping</span>
            <span class="hero-tag">Explainable scores</span>
            <span class="hero-tag">Skill-aware matching</span>
          </div>
        </div>
        ''',
        unsafe_allow_html=True,
    )


def render_how_it_works() -> None:
    st.markdown('<div class="analytics-title">How it works</div>', unsafe_allow_html=True)
    steps = [
        ("01", "Upload your resume", "Your PDF is parsed into contact details, skills, projects, experience, and education."),
        ("02", "Build a targeted search", "Your role and strongest skills become focused search queries across both job platforms."),
        ("03", "Rank by fit", "Jobs are scored using semantic similarity, skills, projects, and experience, then ranked."),
    ]
    cols = st.columns(3, gap="medium")
    for col, (number, title, desc) in zip(cols, steps):
        with col:
            st.markdown(
                f'''
                <div class="how-card">
                  <div class="how-number">{number}</div>
                  <div class="how-title">{title}</div>
                  <div class="how-copy">{desc}</div>
                </div>
                ''',
                unsafe_allow_html=True,
            )


def render_stats(stats: dict) -> None:
    fetched = stats.get("fetched", 0)
    best = stats.get("best", 0)
    avg = stats.get("avg", 0)
    platforms = stats.get("platform_counts", {})

    st.markdown('<div class="analytics-title">Match overview</div>', unsafe_allow_html=True)
    cols = st.columns(4, gap="small")
    cols[0].metric("Jobs analyzed", fetched)
    cols[1].metric("Top matches", len(stats.get("matches", [])))
    cols[2].metric("Best match", f"{best}%")
    cols[3].metric("Average match", f"{avg}%")

    if platforms:
        pills = "".join(f'<span class="source-pill">{_esc(k)} · {v} jobs</span>' for k, v in platforms.items())
        st.markdown(f'<div class="stats-source">{pills}</div>', unsafe_allow_html=True)


def _score_ring(pct: int) -> str:
    pct = max(0, min(100, int(pct)))
    if pct >= 70:
        color, label = "#12b76a", "Strong fit"
    elif pct >= 50:
        color, label = "#f79009", "Good fit"
    else:
        color, label = "#f04438", "Fair fit"
    return (
        f'<div class="score-ring" style="--score:{pct}%;--score-color:{color};"><span class="score-number">{pct}%</span></div>'
        f'<div class="score-label" style="color:{color};">{label}</div>'
    )


def render_job_card(rank: int, match: dict) -> None:
    job = match["job"]
    pct = int(match.get("percentage", 0))
    company = _company_name(job)
    location = _job_location(job)
    url = _apply_url(job)
    source = job.get("source") or ""

    badge = (
        '<span class="badge badge-li">LinkedIn</span>'
        if source == "LinkedIn"
        else '<span class="badge badge-nk">Naukri</span>'
    )

    meta_items = [
        f'<span class="meta-item">⌖ {_esc(company)}</span>',
        f'<span class="meta-item">◉ {_esc(location)}</span>',
    ]
    if job.get("work_mode"):
        meta_items.append(f'<span class="mode-badge">{_esc(job["work_mode"])}</span>')
    if job.get("experience"):
        meta_items.append(f'<span class="meta-item">◷ {_esc(str(job["experience"]))}</span>')
    if job.get("salary") and str(job.get("salary", "")).lower() not in ("not-mentioned", "", "none"):
        meta_items.append(f'<span class="meta-item">₹ {_esc(str(job["salary"]))}</span>')
    if job.get("posted_date"):
        meta_items.append(f'<span class="meta-item">• {_esc(str(job["posted_date"]))}</span>')

    matched_kws = list(match.get("matched_skills") or [])
    if not matched_kws:
        matched_kws = list(match.get("shared_terms") or [])[:8]
    matched_html = "".join(f'<span class="chip-match">{_esc(s)}</span>' for s in matched_kws[:14])
    missing_html = "".join(f'<span class="chip-miss">{_esc(s)}</span>' for s in (match.get("missing_skills") or [])[:8])

    desc = match.get("description") or ""
    desc_preview = ""
    if desc:
        tail = "…" if len(desc) > 900 else ""
        desc_preview = f'<div class="description-box">{_esc(desc[:900])}{tail}</div>'

    with st.container(key=f"job-card-{rank}", border=True):
        st.markdown(
            f'''
            <div class="job-card-inner">
              <div class="job-grid">
                <div class="rank"><div class="rank-num">Rank</div><div class="rank-value">#{rank}</div></div>
                <div>
                  <div class="job-title">{_esc(job.get("job_title") or "Untitled Role")}{badge}</div>
                  <div class="job-company">{_esc(company)}</div>
                  <div class="job-meta-row">{"".join(meta_items)}</div>
                </div>
                <div class="score-box">{_score_ring(pct)}</div>
              </div>
              <div class="card-divider"></div>
              {f'<div class="match-section"><div class="match-label">✓ Why it matches</div><div class="chip-row">{matched_html}</div></div>' if matched_html else ""}
              {f'<div class="match-section"><div class="match-label">△ Skills to build</div><div class="chip-row">{missing_html}</div></div>' if missing_html else ""}
              {desc_preview}
            </div>
            ''',
            unsafe_allow_html=True,
        )

        if desc:
            with st.expander("View full job description"):
                st.markdown(
                    f'<div style="color:#667085;font-size:.76rem;line-height:1.65">{_esc(desc)}</div>',
                    unsafe_allow_html=True,
                )

        st.markdown('<div class="apply-row">', unsafe_allow_html=True)
        if url:
            st.link_button("↗  Open job & apply", url, use_container_width=True)
        else:
            st.button(
                "Apply link unavailable",
                disabled=True,
                use_container_width=True,
                key=f"apply_{rank}_{abs(hash(company)) % 99999}",
            )
        st.markdown('</div>', unsafe_allow_html=True)


def render_results(matches: list, stats: dict) -> None:
    if not matches:
        return

    render_stats(stats)
    st.markdown(
        f'''
        <div class="results-toolbar">
          <div>
            <div class="results-title">Best matches</div>
            <div class="results-sub">Showing the top {len(matches)} roles ranked for your profile.</div>
          </div>
        </div>
        ''',
        unsafe_allow_html=True,
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Rank", "Job Title", "Company", "Platform", "Location", "Experience",
        "Match %", "Matched Skills", "Missing Skills", "Apply URL"
    ])
    for rank, match in enumerate(matches, start=1):
        job = match["job"]
        writer.writerow([
            rank, job.get("job_title", ""), job.get("company", ""), job.get("source", ""),
            job.get("location", ""), job.get("experience", ""), match["percentage"],
            "; ".join(match.get("matched_skills", [])),
            "; ".join(match.get("missing_skills", [])), job.get("url", ""),
        ])

    st.download_button(
        "↓  Export results as CSV",
        buffer.getvalue(),
        file_name="matches.csv",
        mime="text/csv",
        use_container_width=True,
    )

    for rank, match in enumerate(matches, start=1):
        render_job_card(rank, match)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------
def run_pipeline(settings: dict) -> tuple:
    """Run the full search + match pipeline with live progress feedback."""
    profile = settings["profile"]
    progress = st.progress(0.0, text="Preparing…")
    status = st.empty()
    status.markdown('<span class="loader-text">Preparing search…</span>', unsafe_allow_html=True)

    try:
        queries = build_search_queries(profile, settings["preferred_role"], settings["job_type"],
                                       settings["query_count"])
        status.markdown(f'<span class="loader-text">Searching {", ".join(settings["platforms"])}…</span>',
                        unsafe_allow_html=True)
        progress.progress(0.06)

        def on_progress(done, total, message):
            status.markdown(f'<span class="loader-text">{_esc(message)}</span>', unsafe_allow_html=True)
            progress.progress(0.06 + 0.5 * done / max(total, 1))

        jobs_per_query = 15 * settings["max_pages"]  # scale with the pages slider
        jobs = search_jobs(
            queries, settings["location"], settings["platforms"],
            settings["max_pages"], jobs_per_query=jobs_per_query, progress_cb=on_progress,
        )

        if not jobs:
            progress.empty()
            status.empty()
            st.warning(
                "No jobs were returned for this profile. Try adding a preferred role, "
                "choosing a different location, or selecting both platforms."
            )
            return None, None

        status.markdown('<span class="loader-text">Loading AI model (first run downloads it)…</span>',
                        unsafe_allow_html=True)
        progress.progress(0.6)
        matcher.load_model()

        status.markdown('<span class="loader-text">Extracting job requirements…</span>', unsafe_allow_html=True)
        progress.progress(0.7)
        enrich_jobs(jobs, profile.get("skills", []))

        status.markdown('<span class="loader-text">Scoring and ranking matches…</span>', unsafe_allow_html=True)
        progress.progress(0.8)
        
        # UI filter takes absolute strict priority over the resume
        years = settings["years"] if settings["years"] is not None else profile.get("years_experience")
        
        matches = matcher.match_resume_to_jobs(
            profile.get("resume_text", ""), jobs,
            candidate_skills=profile.get("skills", []),
            projects=profile.get("projects", []),
            years=years,
            preferred_role=settings.get("preferred_role", ""),
            top_n=settings["top_n"],
        )

        progress.progress(1.0)
        status.markdown('<span class="loader-text">Done!</span>', unsafe_allow_html=True)
        time.sleep(0.35)
        progress.empty()
        status.empty()

        platform_counts = {}
        for job in jobs:
            platform_counts[job.get("source", "Unknown")] = platform_counts.get(job.get("source", "Unknown"), 0) + 1

        stats = {
            "fetched": len(jobs),
            "matches": matches,
            "best": matches[0]["percentage"] if matches else 0,
            "avg": int(round(sum(m["percentage"] for m in matches) / len(matches))) if matches else 0,
            "platform_counts": platform_counts,
        }
        # Persist this run to Supabase (non-blocking, non-fatal)
        if _SUPA_ENABLED and matches:
            try:
                _supa.save_search_run(profile, settings, matches)
            except Exception as _exc:
                print(f"[supabase] save_search_run: {_exc}")
        return matches, stats
    except Exception as exc:  # noqa: BLE001 — surface anything unexpected, gracefully
        progress.empty()
        status.empty()
        st.error(f"Something went wrong: {exc}")
        return None, None


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
st.markdown(_APP_CSS, unsafe_allow_html=True)

col_nav, col_main = st.columns([0.82, 2.18], gap="large")

with col_nav:
    with st.container(key="control-panel", border=True):
        settings = render_sidebar()

with col_main:
    render_header()

    if settings["submitted"]:
        if settings["uploaded"] is None:
            st.warning("Upload a resume PDF first, then click **Match my jobs**.")
        elif settings["profile"] is None:
            st.error("The resume could not be parsed. Please upload a valid, text-based PDF.")
        else:
            matches, stats = run_pipeline(settings)
            if matches:
                st.session_state["matches"] = matches
                st.session_state["stats"] = stats

    profile = settings["profile"]

    if profile:
        _render_resume_summary(profile)

    if st.session_state.get("matches"):
        render_results(
            st.session_state["matches"],
            st.session_state.get("stats", {}),
        )

    if not st.session_state.get("matches") and not profile:
        render_how_it_works()

st.markdown(
    '<div class="footer">Built with Streamlit · Sentence Transformers · TF-IDF · '
    "Selenium · Naukri + LinkedIn</div>",
    unsafe_allow_html=True,
)