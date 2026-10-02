"""Build the HTML digest and send it via Gmail SMTP or local sendmail."""
import html
import os
import smtplib
import subprocess
from email.message import EmailMessage

E = html.escape


def render(sections, date, errors=()):
    """sections: list of (search_name, [Job]) already sorted by score."""
    total = sum(len(js) for _, js in sections)
    parts = [f"<div style='font-family:system-ui,Arial,sans-serif;max-width:720px;color:#222'>"
             f"<h2 style='margin-bottom:4px'>🐕 Job Hound — {total} new match{'es' * (total != 1)}</h2>"
             f"<div style='color:#777;margin-bottom:16px'>{E(date)}</div>"]
    text = [f"Job Hound — {total} new matches ({date})\n"]
    for name, jobs in sections:
        if not jobs:
            continue
        parts.append(f"<h3 style='border-bottom:1px solid #ddd;padding-bottom:4px'>{E(name)} ({len(jobs)})</h3>")
        text.append(f"\n== {name} ==")
        for j in jobs:
            colour = "#1a7f37" if j.score >= 8 else "#9a6700" if j.score >= 6 else "#777"
            meta = " · ".join(x for x in [j.company, j.location, j.source,
                                         f"deadline {j.deadline}" if j.deadline else ""] if x)
            parts.append(
                f"<div style='margin:0 0 14px'>"
                f"<span style='display:inline-block;min-width:28px;font-weight:bold;color:{colour}'>{j.score}</span>"
                f"<a href='{E(j.url)}' style='font-weight:600'>{E(j.title)}</a>"
                f"<div style='margin-left:28px;color:#555;font-size:13px'>{E(meta)}</div>"
                + (f"<div style='margin-left:28px;font-size:14px'>{E(j.why)}</div>" if j.why else "")
                + "</div>")
            text.append(f"[{j.score}] {j.title}\n    {meta}\n    {j.why}\n    {j.url}")
    if errors:
        note = "Problems this run: " + "; ".join(errors)
        parts.append(f"<p style='color:#b35900;font-size:12px'>{E(note)}</p>")
        text.append("\n" + note)
    parts.append("<p style='color:#999;font-size:12px'>Sources include Remotive (remotive.com), "
                 "INSPIRE-HEP, Arbeitnow, Hacker News, Adzuna.</p></div>")
    return f"Job Hound: {total} new job{'s' * (total != 1)}", "\n".join(text), "".join(parts)


def send(cfg, subject, text, html_body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("from") or cfg["to"]
    msg["To"] = cfg["to"]
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    method = cfg.get("method", "smtp")
    if method == "smtp":
        password = os.environ.get("SMTP_PASSWORD")
        if not password:
            raise RuntimeError("SMTP_PASSWORD not set in .env (use a Gmail App Password)")
        with smtplib.SMTP_SSL(cfg.get("smtp_host", "smtp.gmail.com"), cfg.get("smtp_port", 465), timeout=60) as s:
            s.login(cfg.get("smtp_user") or msg["From"], password)
            s.send_message(msg)
    elif method == "sendmail":
        subprocess.run(["/usr/sbin/sendmail", "-t", "-oi"], input=msg.as_bytes(), check=True)
    else:
        raise ValueError(f"Unknown email method {method!r}")
