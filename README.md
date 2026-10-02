# job-hound

Fetches new vacancies, drops ones it has already shown you, ranks them against
your profile with Claude (or plain keywords), and emails a digest.

Sources: INSPIRE-HEP (academic physics/astro), Remotive, Arbeitnow, HN "Who is hiring",
Adzuna (optional, free key), and any RSS/Atom feed.

## Setup
1. `cp .env.example .env && chmod 600 .env`, then fill in `ANTHROPIC_API_KEY` and `SMTP_PASSWORD`
   (a Gmail App Password from https://myaccount.google.com/apppasswords).
2. Edit `profile:` (and optionally `cv_file:`) plus the searches in `config.yaml`.
3. Try it: `./run.sh --dry-run` → see `logs/` and open `last_digest.html`.
4. Real run: `./run.sh` (sends the email and records jobs in `seen.json`).

## Schedule (cron)
`crontab -e` and add, e.g. daily at 07:30:

    30 7 * * * /cosma7/data/dp004/dc-newm1/job-hound/run.sh

or weekly on Mondays: `30 7 * * 1 ...`. Note: on COSMA, crontabs live on the specific
login node you created them on.

## Flags
`--dry-run` (no email, no state change), `--no-ai` (keyword scoring only), `-v` (debug logs).

## Adding a source
Write a function in `jobhound/sources.py` returning `Job`s and register it in `FETCHERS`.
