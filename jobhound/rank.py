"""Scoring: a cheap keyword pre-filter, then (optionally) Claude ranks the survivors."""
import json
import os
import re
import shutil
import subprocess

MODEL = "claude-opus-5-5"
CHUNK = 60  # jobs per Claude request

SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "score": {"type": "integer", "description": "0-10 fit for the candidate"},
                    "why": {"type": "string", "description": "One sentence on fit / caveats"},
                },
                "required": ["id", "score", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["matches"],
    "additionalProperties": False,
}


def _words(terms):
    return [re.compile(r"\b" + re.escape(t.lower()) + r"\b") for t in terms or []]


def _dedupe_key(j):
    title = re.sub(r"\((?:[a-z]/)+[a-z]\)", "", j.title.lower())  # drop "(f/m/d)"-style tags
    return re.sub(r"[^a-z0-9]+", " ", title).strip(), re.sub(r"[^a-z0-9]+", " ", j.company.lower()).strip()


def dedupe(jobs):
    """Drop repeat postings (same title and company), e.g. one job listed on two boards."""
    seen, kept = set(), []
    for j in jobs:
        key = _dedupe_key(j)
        if key not in seen:
            seen.add(key)
            kept.append(j)
    return kept


def keyword_filter(jobs, search):
    """Keep jobs matching any `include` term and no `exclude` term. Adds a crude score.
    If `locations` is set, the job's location must also mention one of them."""
    inc, exc = _words(search.get("include")), _words(search.get("exclude"))
    title_exc = _words(search.get("exclude_titles"))
    locs = _words(search.get("locations"))
    kept = []
    for j in jobs:
        if locs and not any(p.search(j.location.lower()) for p in locs):
            continue
        text = j.text().lower()
        if any(p.search(text) for p in exc) or any(p.search(j.title.lower()) for p in title_exc):
            continue
        hits = [p.pattern for p in inc if p.search(text)]
        if inc and not hits:
            continue
        title_hits = sum(1 for p in inc if p.search(j.title.lower()))
        j.score = min(10, 3 + len(hits) + 2 * title_hits) if inc else 5
        j.why = "Matched: " + ", ".join(re.sub(r"\\b|\\", "", h) for h in hits[:6]) if hits else ""
        kept.append(j)
    return kept


def _system(profile):
    return (
        "You screen job vacancies for one candidate. Score each vacancy 0-10 for how well it fits "
        "the candidate's profile and stated preferences (10 = apply today, 7 = worth a look, "
        "<=4 = poor fit). Be honest about hard mismatches: wrong seniority, wrong field, location "
        "or visa constraints, expired deadlines. 'why' is one short, specific sentence the "
        "candidate will read in an email digest. Return every id you were given.\n\n"
        f"<candidate_profile>\n{profile}\n</candidate_profile>"
    )


def _listing(chunk):
    jobs = "\n\n".join(
        f"<job id=\"{j.id}\">\nTitle: {j.title}\nOrg: {j.company}\nLocation: {j.location}\n"
        f"Deadline: {j.deadline or 'n/a'}\nTags: {', '.join(j.tags)}\n{j.description[:1500]}\n</job>"
        for j in chunk
    )
    return f"Score these {len(chunk)} vacancies:\n\n{jobs}"


def _apply(jobs, matches):
    by_id = {j.id: j for j in jobs}
    for m in matches:
        if m["id"] in by_id:
            by_id[m["id"]].score = m["score"]
            by_id[m["id"]].why = m["why"]


def claude_rank(jobs, profile, backend="claude_cli", model="", effort="medium"):
    """Score jobs with Claude. Mutates jobs with .score/.why. Returns token usage totals."""
    usage = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "calls": 0}
    for i in range(0, len(jobs), CHUNK):
        chunk = jobs[i:i + CHUNK]
        call = _rank_cli if backend == "claude_cli" else _rank_api
        matches, u = call(_system(profile), _listing(chunk), model, effort)
        _apply(chunk, matches)
        for k in usage:
            usage[k] += u.get(k, 0)
        usage["calls"] += 1
    return usage


def _rank_cli(system, prompt, model, effort):
    """Via the Claude Code CLI: billed to your Claude subscription, not the API."""
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    cmd = [exe, "-p", "--output-format", "json", "--no-session-persistence",
           "--tools", "", "--strict-mcp-config", "--setting-sources", "",
           "--system-prompt", system, "--json-schema", json.dumps(SCHEMA)]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    # An API key in the environment would make the CLI bill the API instead of your plan.
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=900, env=env)
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI failed (exit {r.returncode}): {(r.stderr or r.stdout)[-300:]}")
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(f"claude CLI error: {str(out.get('result'))[:300]}")
    u = out.get("usage", {})
    return out["structured_output"]["matches"], {
        "input": u.get("input_tokens", 0), "cache_write": u.get("cache_creation_input_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0)}


def _rank_api(system, prompt, model, effort):
    """Via the Anthropic API: needs ANTHROPIC_API_KEY, billed per token."""
    import anthropic
    resp = anthropic.Anthropic().beta.messages.create(
        model=model or MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"Claude stopped with {resp.stop_reason}")
    text = next(b.text for b in resp.content if b.type == "text")
    u = resp.usage
    return json.loads(text)["matches"], {
        "input": u.input_tokens, "cache_write": u.cache_creation_input_tokens or 0,
        "cache_read": u.cache_read_input_tokens or 0, "output": u.output_tokens}
