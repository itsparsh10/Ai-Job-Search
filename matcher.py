"""
matcher.py — Matching logic only.

This module is responsible for ONE thing: comparing a resume against a list
of jobs and ranking them by similarity. It contains:

    • Semantic similarity   (Sentence Transformers + TF-IDF cosine)
    • Skill matching        (candidate skills vs skills required by the job)
    • Project similarity    (resume projects vs job description)
    • Experience matching   (candidate years vs experience required by the job)
    • Dynamic weighting     (weights shift based on the candidate's experience)
    • Explainable scoring   (every score returns matched / missing skills)
    • Ranking               (best match first, latest jobs first on ties)

Resume parsing, skill extraction and job scraping live in app.py, which is
the central file. This file intentionally imports none of them.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
TOP_N_JOBS = 10
MIN_SCORE = 0.30  # jobs below this are never shown — avoids irrelevant results

# "auto" → use Sentence Transformers when available, fall back to TF-IDF.
# "off"  → always use TF-IDF only (no model download).
EMBEDDING_MODE = os.environ.get("AI_MATCHER_EMBEDDINGS", "auto")

_model = None  # SentenceTransformer instance — loaded once, reused everywhere


# --------------------------------------------------------------------------
# Embeddings & semantic similarity
# --------------------------------------------------------------------------
def load_model():
    """Load the sentence-transformer model once and reuse it.

    Returns None when embeddings are disabled or the model cannot be loaded
    (e.g. no internet on first run); matching then falls back to TF-IDF.
    """
    global _model
    if EMBEDDING_MODE == "off":
        return None
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_NAME)
            print("[matcher] sentence-transformer model loaded")
        except Exception as exc:  # noqa: BLE001 — offline / broken install
            print(f"[matcher] embeddings unavailable ({exc}); using TF-IDF only")
            _model = False
    return _model if _model else None


def embed_texts(texts: list[str]) -> Optional[np.ndarray]:
    """Generate normalized embeddings for a list of texts (or None)."""
    model = load_model()
    if model is None:
        return None
    return model.encode(
        texts, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True
    )


def _tfidf_scores(resume_text: str, texts: list[str]) -> np.ndarray:
    """Cosine similarity of a source text against each text, using TF-IDF."""
    if not texts:
        return np.array([])
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    try:
        matrix = vectorizer.fit_transform([resume_text] + list(texts))
        return cosine_similarity(matrix[0:1], matrix[1:])[0]
    except ValueError:
        # Empty vocabulary (no shared words) — score everything at zero.
        return np.zeros(len(texts))


def compute_similarity(resume_text: str, descriptions: list[str]) -> np.ndarray:
    """Return similarity between a source text and each description.

    Blends sentence-transformer similarity (semantic understanding) with
    TF-IDF similarity (exact keyword overlap) so the score is robust even
    when the model is not available.
    """
    if not descriptions:
        return np.array([])
    tfidf = _tfidf_scores(resume_text, descriptions)

    embeddings = embed_texts([resume_text] + list(descriptions))
    if embeddings is None:
        return tfidf

    sbert = cosine_similarity(embeddings[0:1], embeddings[1:])[0]
    # PONYTAIL: 65/35 blend; ceiling = corpus too small for good IDF.
    # Upgrade: raise sbert to 1.0 once embeddings are stable.
    return 0.65 * sbert + 0.35 * tfidf


# --------------------------------------------------------------------------
# Weighted component scores
# --------------------------------------------------------------------------
def _weights(years: Optional[float]) -> dict:
    """Return component weights based on the candidate's experience.

    Dynamic rule: the more experience a candidate has, the more their
    experience matters; fresh graduates lean on skills and projects.
    Title match is a constant 10% across all levels — penalises role mismatches
    (e.g. Data Engineer resume vs Accountant job).
    All rows sum to 1.0.
    """
    y = years or 0.0
    if y < 1:     # Fresher
        return {"semantic": 0.20, "skills": 0.35, "projects": 0.20, "experience": 0.15, "title": 0.10}
    if y < 3:     # Junior
        return {"semantic": 0.20, "skills": 0.30, "projects": 0.17, "experience": 0.23, "title": 0.10}
    if y < 6:     # Mid-level
        return {"semantic": 0.20, "skills": 0.27, "projects": 0.10, "experience": 0.33, "title": 0.10}
    return {"semantic": 0.20, "skills": 0.22, "projects": 0.05, "experience": 0.43, "title": 0.10}  # Senior


def _skill_score(candidate_skills: list[str], job_skills: list[str]) -> float:
    """Score how well the candidate's skills cover the job's required skills."""
    candidate = {s.lower() for s in (candidate_skills or [])}
    job = {s.lower() for s in (job_skills or [])}
    if not job:
        return 0.75  # unknown requirement — neutral score
    matched = candidate & job
    precision = len(matched) / len(job)          # how many required skills are covered
    coverage = len(matched) / len(candidate) if candidate else 0.0  # how much of the resume is used
    return round(0.75 * precision + 0.25 * min(1.0, coverage), 4)


def parse_experience(text: str):
    """Parse a job's experience text like '3-6 Yrs' or '2+ Years'. Returns (low, high)."""
    if not text:
        return None, None
    match = re.search(r"(\d+)\s*[-–—to]+\s*(\d+)\s*(?:years?|yrs)", text, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"(\d+)\s*\+?\s*(?:years?|yrs)", text, re.IGNORECASE)
    if match:
        return int(match.group(1)), None
    return None, None


def _experience_score(years: Optional[float], experience_text: str) -> float:
    """Score how well the candidate's experience fits the job's requirement."""
    if years is None:
        return 0.75  # unknown — neutral
    low, high = parse_experience(experience_text)
    if low is None:
        return 0.75  # job does not state a requirement — neutral
    if years >= (high if high is not None else low):
        return 1.0
    if high is not None and years >= low:
        return 0.9
    # Partial credit: the closer the candidate is to the requirement, the better.
    return round(max(0.0, 1.0 - (low - years) / max(low, 1)), 4)


def _projects_summary(projects) -> str:
    """Flatten the parsed project list into one text blob for comparison."""
    if not projects:
        return ""
    parts = []
    for project in projects:
        if isinstance(project, str):
            parts.append(project)
        else:
            parts.append(" ".join(str(v) for v in project.values() if v))
    return " ".join(parts)


# --------------------------------------------------------------------------
# Shared terms & explanations
# --------------------------------------------------------------------------
_STOPWORDS = {
    "with", "this", "that", "have", "from", "your", "will", "work", "role",
    "team", "company", "experience", "ability", "including", "other", "must",
    "you", "and", "the", "for", "are", "our", "all", "can", "who", "what",
    "about", "into", "over", "they", "their", "them", "well", "also", "plus",
}


def shared_terms(resume_text: str, description: str, top_k: int = 6) -> list[str]:
    """Return the most meaningful keywords shared by resume and description."""
    def words(text: str) -> list[str]:
        return [re.sub(r"[^a-z0-9+#.]", "", w) for w in text.lower().split()]

    resume_words = {w for w in words(resume_text) if len(w) > 3 and w not in _STOPWORDS}
    description_words = [w for w in words(description) if w in resume_words]
    counts: dict[str, int] = {}
    for word in description_words:
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [word for word, _ in ranked[:top_k]]


def _explanation(pct, total, matched, missing, weights, sem, skill, proj, experience, title=0.0) -> str:
    """Human-readable reason behind a match score (never a random number)."""
    lines = [
        f"{pct}% = {total:.2%} weighted match — semantic {sem:.0%}, "
        f"skills {skill:.0%}, projects {proj:.0%}, experience {experience:.0%}, title {title:.0%}.",
        f"Weights for your level: skills {weights['skills']:.0%}, "
        f"projects {weights['projects']:.0%}, experience {weights['experience']:.0%}, "
        f"title {weights['title']:.0%}, semantic {weights['semantic']:.0%}.",
    ]
    if matched:
        lines.append("Matched: " + ", ".join(str(s) for s in matched[:8]))
    if missing:
        lines.append("Missing: " + ", ".join(str(s) for s in missing[:8]))
    return " ".join(lines)


def _title_match_score(preferred_role: str, job_title: str) -> float:
    """Score how well the job title aligns with the candidate's preferred role.

    Uses word-level F1 between role words and title words so 'Python Developer'
    scores well against 'Senior Python Developer' but not 'Java Developer'.
    Returns 0.75 (neutral) when no preferred role is stated.
    """
    if not preferred_role or not preferred_role.strip():
        return 0.75  # no preference → neutral, not zero
    role_words = set(re.sub(r"[^a-z\s]", "", preferred_role.lower()).split()) - _STOPWORDS
    title_words = set(re.sub(r"[^a-z\s]", "", job_title.lower()).split()) - _STOPWORDS
    if not role_words:
        return 0.75
    if not title_words:
        return 0.40  # job has no parseable title — soft penalty
    matched_words = role_words & title_words
    precision = len(matched_words) / len(role_words)
    recall = len(matched_words) / len(title_words)
    if precision + recall == 0:
        return 0.0
    f1 = 2 * precision * recall / (precision + recall)
    return round(f1, 4)


# --------------------------------------------------------------------------
# Ranking helpers
# --------------------------------------------------------------------------
def _posted_timestamp(job: dict) -> float:
    """Convert a job's posted_date string into an epoch timestamp (0 when unknown)."""
    value = str(job.get("posted_date") or "").strip()
    if not value:
        return 0.0
    try:
        # ISO / machine-readable date
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass

    lowered = value.lower()
    now = datetime.now()
    if lowered in {"today", "just now", "just posted", "1 day ago"}:
        return now.timestamp()
    match = re.search(r"(\d+)\s*\+?\s+(day|week|month)s?\s+ago", lowered)
    if match:
        number, unit = int(match.group(1)), match.group(2)
        delta = {"day": timedelta(days=number), "week": timedelta(weeks=number),
                 "month": timedelta(days=number * 30)}[unit]
        return (now - delta).timestamp()
    return 0.0


# --------------------------------------------------------------------------
# Main matching entry point
# --------------------------------------------------------------------------
def match_resume_to_jobs(
    resume_text: str,
    jobs: list[dict],
    candidate_skills: Optional[list[str]] = None,
    projects: Optional[list] = None,
    years: Optional[float] = None,
    preferred_role: str = "",
    top_n: int = TOP_N_JOBS,
) -> list[dict]:
    """Score, explain and rank every job against the resume.

    Returns a list of match dicts, best first:

        {
            "job", "description", "score", "percentage",
            "semantic_score", "skill_score", "project_score", "experience_score",
            "matched_skills", "missing_skills", "explanation", "shared_terms"
        }
    """
    jobs_with_text = []
    for job in jobs:
        description = (job.get("description") or "").strip()
        title = (job.get("job_title") or "").strip()
        text = f"{title} {description}".strip() or title
        if text:
            jobs_with_text.append((job, description, text))
    if not jobs_with_text:
        return []

    texts = [t for _, _, t in jobs_with_text]

    # Semantic similarity (resume ↔ job) and project similarity (projects ↔ job)
    semantic = compute_similarity(resume_text, texts)
    projects_blob = _projects_summary(projects)
    if projects_blob.strip():
        project_scores = compute_similarity(projects_blob, texts)
    else:
        project_scores = np.full(len(texts), 0.5)  # no projects — neutral

    weights = _weights(years)

    results = []
    for (job, description, _), sem, proj in zip(jobs_with_text, semantic, project_scores):
        
        # Strict Experience Filter
        if years is not None:
            low, high = parse_experience(job.get("experience") or "")
            if low is not None:
                # If the job strictly demands significantly more experience than the user has, filter it out completely.
                # (e.g., if user is a Fresher (0 yrs) and job requires 3+, drop it).
                if low > years + 2:
                    continue

        job_skills = [str(s) for s in (job.get("job_skills") or [])]
        candidate_set = {s.lower() for s in (candidate_skills or [])}

        skill = _skill_score(candidate_skills, job_skills)
        experience = _experience_score(years, job.get("experience") or "")

        title = _title_match_score(preferred_role, job.get("job_title", ""))
        total = (
            weights["semantic"] * float(sem)
            + weights["skills"] * skill
            + weights["projects"] * float(proj)
            + weights["experience"] * experience
            + weights["title"] * title
        )
        total = max(0.0, min(1.0, total))
        if total < MIN_SCORE:
            continue  # drop irrelevant jobs — they never reach the UI
        pct = min(99, int(round(total * 100)))

        matched = [s for s in job_skills if s.lower() in candidate_set]
        missing = [s for s in job_skills if s.lower() not in candidate_set]

        results.append(
            {
                "job": job,
                "description": description,
                "score": round(float(total), 4),
                "percentage": pct,
                "semantic_score": round(float(sem), 4),
                "skill_score": round(float(skill), 4),
                "project_score": round(float(proj), 4),
                "experience_score": round(float(experience), 4),
                "title_score": round(float(title), 4),
                "matched_skills": matched,
                "missing_skills": missing,
                "explanation": _explanation(
                    pct, total, matched, missing, weights,
                    float(sem), skill, float(proj), experience, float(title)
                ),
                "shared_terms": shared_terms(resume_text, description),
                "_posted_ts": _posted_timestamp(job),
            }
        )

    # Rank: best score first, then latest jobs, then strongest skill match.
    results.sort(key=lambda m: (-m["score"], -m.pop("_posted_ts"), -m["skill_score"]))
    return results[:top_n]
