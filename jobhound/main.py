"""job-hound: fetch vacancies, drop ones already seen, rank them, email a digest.

    python -m jobhound.main                  # normal run (emails, updates state)
    python -m jobhound.main --dry-run        # print digest, no email, no state change
    python -m jobhound.main --no-ai          # keyword scoring only
"""
import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path

import anthropic
import yaml

from . import mailer, rank
from .sources import FETCHERS

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("jobhound")


def load_env(path):
    """Minimal .env loader: KEY=value lines, # comments."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def load_state(path):
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(path, seen, keep_days=180):
    cutoff = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen, indent=0, sort_keys=True))
    tmp.replace(path)


def fetch_all(sources):
    jobs, errors = {}, []
    for src in sources:
        kind = src["type"]
        try:
            got = FETCHERS[kind](src)
            log.info("%-10s %4d jobs", kind, len(got))
            for j in got:
                jobs.setdefault(j.id, j)
        except Exception as e:  # one broken source shouldn't kill the digest
            log.warning("%s failed: %s", kind, e)
            errors.append(f"{src.get('name', kind)}: {e}")
    return list(jobs.values()), errors


def profile_text(cfg):
    text = cfg.get("profile", "")
    if cfg.get("cv_file"):
        cv = ROOT / cfg["cv_file"]
        if cv.exists():
            text += "\n\nCV:\n" + read_cv(cv)[:20000]
    return text


def read_cv(path):
    """Plain text/markdown as-is; PDFs via pypdf (only needed if the CV is a PDF)."""
    if path.suffix.lower() != ".pdf":
        return path.read_text()
    from pypdf import PdfReader
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def run(args):
    load_env(ROOT / ".env")
    cfg = yaml.safe_load((ROOT / args.config).read_text())
    state_path = ROOT / cfg.get("state_file", "seen.json")
    seen = load_state(state_path)
    today = dt.date.today().isoformat()
    ai = cfg.get("ai", {})
    backend = ai.get("backend", "claude_cli")
    use_ai = ai.get("enabled", True) and not args.no_ai
    if use_ai and backend == "api" and not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY not set; falling back to keyword scoring")
        use_ai = False
    usage_total = {}

    sections, all_errors, newly_seen = [], [], {}
    for search in cfg["searches"]:
        name = search["name"]
        jobs, errors = fetch_all(search["sources"])
        all_errors += errors
        fresh = [j for j in jobs if j.id not in seen]
        cands = rank.dedupe(rank.keyword_filter(fresh, search))
        cands.sort(key=lambda j: j.score, reverse=True)
        cands = cands[: ai.get("max_candidates", 120)]
        log.info("[%s] %d fetched, %d new, %d pass keywords", name, len(jobs), len(fresh), len(cands))

        if use_ai and cands:
            try:
                u = rank.claude_rank(cands, profile_text(cfg) + f"\n\nThis search: {search.get('notes', name)}",
                                     backend=backend, model=ai.get("model", ""), effort=ai.get("effort", "medium"))
                log.info("[%s] Claude usage: %s", name, u)
                for k, v in u.items():
                    usage_total[k] = usage_total.get(k, 0) + v
            except (anthropic.APIError, RuntimeError, ValueError, KeyError) as e:
                log.warning("Claude ranking failed (%s); using keyword scores", e)
                all_errors.append(f"Claude ranking for {name}: {e}")

        min_score = search.get("min_score", cfg.get("min_score", 6 if use_ai else 4))
        picks = sorted((j for j in cands if j.score >= min_score), key=lambda j: j.score, reverse=True)
        sections.append((name, picks[: search.get("max_results", 25)]))
        # Everything fetched counts as seen, so rejected jobs aren't re-scored tomorrow.
        newly_seen.update({j.id: today for j in fresh})

    subject, text, html_body = mailer.render(sections, today, all_errors)
    total = sum(len(p) for _, p in sections)
    if usage_total:
        log.info("Claude usage this run: %s", usage_total)

    if args.dry_run:
        print(text)
        (ROOT / "last_digest.html").write_text(html_body)
        log.info("Dry run: digest written to last_digest.html, nothing sent, state unchanged")
        return
    if total or cfg["email"].get("send_when_empty", False):
        mailer.send(cfg["email"], subject, text, html_body)
        log.info("Sent: %s", subject)
    else:
        log.info("No new matches; no email sent")
    seen.update(newly_seen)
    save_state(state_path, seen)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-ai", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    run(args)


if __name__ == "__main__":
    main()
