"""Job sources. Each fetcher takes its config block and returns a list of Job."""
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests

UA = {"User-Agent": "job-hound/1.0 (personal job alert script)"}
TIMEOUT = 30


@dataclass
class Job:
    id: str  # globally unique, prefixed with the source name
    title: str
    url: str
    source: str
    company: str = ""
    location: str = ""
    posted: str = ""
    deadline: str = ""
    description: str = ""
    tags: list = field(default_factory=list)
    score: int = 0
    why: str = ""

    def text(self):
        return " ".join([self.title, self.company, self.location, self.description, " ".join(self.tags)])


def strip_html(s, limit=3000):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", html.unescape(s)).strip()
    return s[:limit]


def _get(url, **params):
    r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def inspire(cfg):
    """INSPIRE-HEP jobs: physics/astro academic positions worldwide."""
    q = cfg.get("query", "")
    data = _get("https://inspirehep.net/api/jobs", q=q, sort="mostrecent",
                size=cfg.get("max", 100)).json()
    jobs = []
    for hit in data["hits"]["hits"]:
        m = hit["metadata"]
        if m.get("status") not in (None, "open"):
            continue
        cn = m.get("control_number") or hit["id"]
        jobs.append(Job(
            id=f"inspire:{cn}",
            title=m.get("position", ""),
            url=f"https://inspirehep.net/jobs/{cn}",
            source="INSPIRE",
            company=", ".join(i.get("value", "") for i in m.get("institutions", [])),
            location=", ".join(m.get("regions", [])),
            posted=(hit.get("created") or "")[:10],
            deadline=m.get("deadline_date", ""),
            description=strip_html(m.get("description")),
            tags=m.get("ranks", []) + m.get("arxiv_categories", []),
        ))
    return jobs


def remotive(cfg):
    """Remotive: remote tech jobs. Jobs must link back to Remotive (we do)."""
    params = {"limit": cfg.get("max", 100)}
    if cfg.get("search"):
        params["search"] = cfg["search"]
    if cfg.get("category"):
        params["category"] = cfg["category"]
    data = _get("https://remotive.com/api/remote-jobs", **params).json()
    return [Job(
        id=f"remotive:{j['id']}",
        title=j["title"],
        url=j["url"],
        source="Remotive",
        company=j.get("company_name", ""),
        location=j.get("candidate_required_location", "") + (f" · {j['salary']}" if j.get("salary") else ""),
        posted=(j.get("publication_date") or "")[:10],
        description=strip_html(j.get("description")),
        tags=j.get("tags", []),
    ) for j in data.get("jobs", [])]


def arbeitnow(cfg):
    """Arbeitnow: mostly Europe/Germany tech jobs, many remote."""
    jobs = []
    for page in range(1, cfg.get("pages", 3) + 1):
        data = _get("https://www.arbeitnow.com/api/job-board-api", page=page).json()
        for j in data.get("data", []):
            jobs.append(Job(
                id=f"arbeitnow:{j['slug']}",
                title=j["title"],
                url=j["url"],
                source="Arbeitnow",
                company=j.get("company_name", ""),
                location=j.get("location", "") + (" (remote)" if j.get("remote") else ""),
                description=strip_html(j.get("description")),
                tags=j.get("tags", []) + j.get("job_types", []),
            ))
        if not data.get("links", {}).get("next"):
            break
    return jobs


def hn_hiring(cfg):
    """Top-level comments in the latest Hacker News 'Ask HN: Who is hiring?' thread."""
    stories = _get("https://hn.algolia.com/api/v1/search_by_date",
                   tags="story,author_whoishiring", hitsPerPage=10).json()["hits"]
    thread = next((s for s in stories if "who is hiring" in s["title"].lower()), None)
    if not thread:
        return []
    item = _get(f"https://hn.algolia.com/api/v1/items/{thread['objectID']}").json()
    jobs = []
    for c in item.get("children", []):
        text = strip_html(c.get("text"))
        if not text:
            continue
        fields = [p.strip() for p in text.split("|")]
        jobs.append(Job(
            id=f"hn:{c['id']}",
            title=" | ".join(fields[:4])[:160],
            url=f"https://news.ycombinator.com/item?id={c['id']}",
            source="HN Who's Hiring",
            company=fields[0][:80],
            posted=(c.get("created_at") or "")[:10],
            description=text,
        ))
    return jobs


def adzuna(cfg):
    """Adzuna aggregator (UK/US/EU, industry). Needs free API keys from developer.adzuna.com."""
    import os
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY not set in .env")
    params = {"app_id": app_id, "app_key": app_key, "results_per_page": 50,
              "max_days_old": cfg.get("max_days_old", 7), "sort_by": "date"}
    for k in ("what", "what_or", "what_phrase", "where", "distance"):
        if cfg.get(k):
            params[k] = cfg[k]
    results = []
    for page in range(1, cfg.get("pages", 1) + 1):
        data = _get(f"https://api.adzuna.com/v1/api/jobs/{cfg.get('country', 'gb')}/search/{page}", **params).json()
        results += data.get("results", [])
        if len(data.get("results", [])) < params["results_per_page"]:
            break
    jobs = []
    for j in results:
        salary = ""
        if j.get("salary_min"):
            salary = f" · £{int(j['salary_min']):,}" + (f"–{int(j['salary_max']):,}" if j.get("salary_max") else "")
        jobs.append(Job(
            id=f"adzuna:{j['id']}",
            title=strip_html(j["title"]),
            url=j["redirect_url"],
            source="Adzuna",
            company=j.get("company", {}).get("display_name", ""),
            location=j.get("location", {}).get("display_name", "") + salary,
            posted=(j.get("created") or "")[:10],
            description=strip_html(j.get("description")),
        ))
    return jobs


def rss(cfg):
    """Any RSS/Atom feed (university vacancy pages, AAS, jobs.ac.uk alerts, etc.)."""
    root = ET.fromstring(_get(cfg["url"]).content)
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.iter("item") if root.find(".//item") is not None else root.iter(f"{atom}entry")
    name = cfg.get("name", "RSS")
    jobs = []
    for it in items:
        def t(tag):
            el = it.find(tag)
            if el is None:
                el = it.find(atom + tag)
            return (el.text or "") if el is not None else ""
        link = t("link")
        if not link:
            el = it.find(f"{atom}link")
            link = el.get("href", "") if el is not None else ""
        guid = t("guid") or t("id") or link
        jobs.append(Job(
            id=f"rss:{name}:{guid}",
            title=strip_html(t("title")),
            url=link.strip(),
            source=name,
            posted=(t("pubDate") or t("updated"))[:25],
            description=strip_html(t("description") or t("summary") or t("content")),
        ))
    return jobs


FETCHERS = {
    "inspire": inspire,
    "remotive": remotive,
    "arbeitnow": arbeitnow,
    "hn_hiring": hn_hiring,
    "adzuna": adzuna,
    "rss": rss,
}
