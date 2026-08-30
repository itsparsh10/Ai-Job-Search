"""
supabase_client.py — Supabase persistence layer for AI Resume Matcher.

Responsibilities:
    • Cache parsed resume profiles (keyed by SHA-256 of PDF bytes)
    • Store raw PDF files in Supabase Storage bucket "resumes"
    • Persist search runs + match results for history / analytics

All public functions are non-fatal: they catch every exception and log it
so the Streamlit app never crashes due to a DB / network issue.

Environment variables required (in .env):
    SUPABASE_URL  — your project URL
    SUPABASE_KEY  — publishable / anon key
"""

from __future__ import annotations

import os
import datetime
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_client = None  # lazy singleton


def _get_client():
    """Return (and cache) the Supabase client. Raises if not configured."""
    global _client
    if _client is None:
        if not _SUPABASE_URL or not _SUPABASE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_KEY must be set in .env to use persistence."
            )
        from supabase import create_client
        _client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
    return _client


# --------------------------------------------------------------------------
# Resume cache
# --------------------------------------------------------------------------

def get_cached_resume(file_hash: str) -> Optional[dict]:
    """Return a previously parsed resume profile from Supabase, or None."""
    try:
        sb = _get_client()
        result = (
            sb.table("resumes")
            .select("parsed_json")
            .eq("file_hash", file_hash)
            .limit(1)
            .execute()
        )
        if result.data:
            print(f"[supabase] resume cache HIT — hash {file_hash[:12]}…")
            return result.data[0]["parsed_json"]
    except Exception as exc:
        print(f"[supabase] get_cached_resume failed: {exc}")
    return None


def save_resume(file_hash: str, profile: dict, pdf_bytes: bytes) -> None:
    """Upsert the parsed profile + upload the raw PDF to Storage."""
    try:
        sb = _get_client()

        # 1. Store parsed JSON in the resumes table
        sb.table("resumes").upsert(
            {
                "file_hash": file_hash,
                "parsed_json": profile,
                "candidate_name": profile.get("name", ""),
                "candidate_email": profile.get("email", ""),
                "years_experience": profile.get("years_experience", 0),
                "skills": profile.get("skills", []),
                "updated_at": datetime.datetime.utcnow().isoformat(),
            },
            on_conflict="file_hash",
        ).execute()
        print(f"[supabase] resume JSON saved — {profile.get('name', 'unknown')}")

        # 2. Upload raw PDF to Storage bucket "resumes"
        storage_path = f"{file_hash}.pdf"
        try:
            sb.storage.from_("resumes").upload(
                path=storage_path,
                file=pdf_bytes,
                file_options={"content-type": "application/pdf", "upsert": "true"},
            )
            print(f"[supabase] PDF uploaded → resumes/{storage_path}")
        except Exception as exc:
            print(f"[supabase] PDF upload skipped: {exc}")

    except Exception as exc:
        print(f"[supabase] save_resume failed: {exc}")


# --------------------------------------------------------------------------
# Search run + match history
# --------------------------------------------------------------------------

def save_search_run(profile: dict, settings: dict, matches: list) -> None:
    """Persist a completed search run and its top match results."""
    try:
        sb = _get_client()

        run_resp = (
            sb.table("search_runs")
            .insert(
                {
                    "candidate_name": profile.get("name", ""),
                    "candidate_email": profile.get("email", ""),
                    "preferred_role": settings.get("preferred_role", ""),
                    "location": settings.get("location", ""),
                    "platforms": settings.get("platforms", []),
                    "years_exp": settings.get("years", 0),
                    "job_type": settings.get("job_type", "Full-time"),
                    "total_matches": len(matches),
                    "best_score": matches[0]["percentage"] if matches else 0,
                    "ran_at": datetime.datetime.utcnow().isoformat(),
                }
            )
            .execute()
        )

        if not run_resp.data:
            print("[supabase] search_runs insert returned no data")
            return

        run_id = run_resp.data[0]["id"]
        print(f"[supabase] search_run saved (id={run_id})")

        rows = []
        for m in matches[:20]:
            job = m.get("job", {})
            rows.append(
                {
                    "run_id": run_id,
                    "job_title": job.get("job_title", ""),
                    "company": job.get("company", ""),
                    "platform": job.get("source", ""),
                    "location": job.get("location", ""),
                    "job_url": job.get("url", ""),
                    "percentage": m.get("percentage", 0),
                    "semantic_score": m.get("semantic_score", 0),
                    "skill_score": m.get("skill_score", 0),
                    "experience_score": m.get("experience_score", 0),
                    "title_score": m.get("title_score", 0),
                    "matched_skills": m.get("matched_skills", []),
                    "missing_skills": m.get("missing_skills", []),
                }
            )

        if rows:
            sb.table("match_results").insert(rows).execute()
            print(f"[supabase] {len(rows)} match rows saved for run {run_id}")

    except Exception as exc:
        print(f"[supabase] save_search_run failed: {exc}")
