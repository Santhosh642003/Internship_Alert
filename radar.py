#!/usr/bin/env python3
"""Internship Radar — pulls fresh internship postings, filters for fit + work
authorization, and pushes new matches to Telegram. Built to run on a schedule
(GitHub Actions) with zero servers."""

import json, os, sys, re, time, html
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "config.json")))
SEEN_PATH = os.path.join(HERE, "seen.json")

SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json"

# phrases that signal a role won't take CPT / needs citizenship
NO_SPONSOR_PATTERNS = [
    "does not offer sponsorship", "will not sponsor", "no visa sponsorship",
    "without sponsorship", "not provide sponsorship", "unable to sponsor",
    "must be authorized to work in the united states without",
    "u.s. citizen", "us citizen", "security clearance", "citizenship is required",
]


def get_json(url, timeout=30):
    req = Request(url, headers={"User-Agent": "internship-radar/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_seen():
    try:
        return set(json.load(open(SEEN_PATH)))
    except Exception:
        return set()


def save_seen(seen):
    json.dump(sorted(seen), open(SEEN_PATH, "w"))


def text_has(haystack, needles):
    h = (haystack or "").lower()
    return any(n in h for n in needles)


FOREIGN = ["canada", "germany", "united kingdom", " uk", "india", "ireland",
           "france", "singapore", "london", "berlin", "toronto", "australia",
           "netherlands", "israel", "japan", "spain"]


def location_ok(locations):
    locs = " ".join(locations).lower() if locations else ""
    us_terms = [l for l in CONFIG["locations_allow"] if l != "remote"]
    us_signal = any(l in locs for l in us_terms) or " usa" in locs or "united states" in locs
    foreign = any(f in locs for f in FOREIGN)
    if CONFIG["allow_remote"] and "remote" in locs and not (foreign and not us_signal):
        return True
    if CONFIG["allow_us_wide"] and not foreign:
        return True
    if foreign and not us_signal:
        return False
    return any(l in locs for l in CONFIG["locations_allow"])


# ---------- sources ----------
def fetch_simplify():
    out = []
    try:
        data = get_json(SIMPLIFY_URL)
    except (URLError, HTTPError, ValueError) as e:
        print(f"[simplify] fetch failed: {e}")
        return out
    cats = set(CONFIG["categories"])
    terms = set(CONFIG["terms_allow"])
    for j in data:
        if not (j.get("active") and j.get("is_visible")):
            continue
        if cats and j.get("category") not in cats:
            continue
        if terms and not (set(j.get("terms") or []) & terms):
            continue
        title = j.get("title", "")
        if CONFIG["role_keywords"] and not text_has(title, CONFIG["role_keywords"]) \
           and j.get("category") not in ("AI/ML/Data", "Data Science, AI & Machine Learning"):
            continue
        if not location_ok(j.get("locations")):
            continue
        sp = (j.get("sponsorship") or "Other")
        if CONFIG["exclude_no_sponsorship"] and sp in ("Does Not Offer Sponsorship", "U.S. Citizenship is Required"):
            continue
        out.append({
            "id": "simplify:" + j["id"],
            "title": title,
            "company": j.get("company_name", ""),
            "location": ", ".join(j.get("locations") or []) or "n/a",
            "url": j.get("url", ""),
            "source": "Simplify",
            "sponsorship": sp,
        })
    return out


def _ats_keep(title, desc):
    if CONFIG["role_keywords"] and not text_has(title, CONFIG["role_keywords"]):
        return None
    if "intern" not in title.lower():
        return None
    flag = "Does Not Offer Sponsorship" if text_has(desc, NO_SPONSOR_PATTERNS) else "Other"
    if CONFIG["exclude_no_sponsorship"] and flag != "Other":
        return None
    return flag


def fetch_greenhouse():
    out = []
    for tok in CONFIG["sources"]["greenhouse_tokens"]:
        try:
            data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs?content=true")
        except Exception as e:
            print(f"[greenhouse:{tok}] failed: {e}"); continue
        for j in data.get("jobs", []):
            title = j.get("title", "")
            loc = (j.get("location") or {}).get("name", "")
            desc = html.unescape(re.sub("<[^>]+>", " ", j.get("content", "")))
            flag = _ats_keep(title, desc)
            if flag is None: continue
            if not location_ok([loc]): continue
            out.append({"id": f"gh:{tok}:{j['id']}", "title": title, "company": tok.title(),
                        "location": loc or "n/a", "url": j.get("absolute_url", ""),
                        "source": "Greenhouse", "sponsorship": flag})
    return out


def fetch_lever():
    out = []
    for tok in CONFIG["sources"]["lever_tokens"]:
        try:
            data = get_json(f"https://api.lever.co/v0/postings/{tok}?mode=json")
        except Exception as e:
            print(f"[lever:{tok}] failed: {e}"); continue
        for j in data:
            title = j.get("text", "")
            loc = (j.get("categories") or {}).get("location", "")
            desc = html.unescape(re.sub("<[^>]+>", " ", j.get("descriptionPlain", j.get("description", ""))))
            flag = _ats_keep(title, desc)
            if flag is None: continue
            if not location_ok([loc]): continue
            out.append({"id": f"lever:{tok}:{j['id']}", "title": title, "company": tok.title(),
                        "location": loc or "n/a", "url": j.get("hostedUrl", ""),
                        "source": "Lever", "sponsorship": flag})
    return out


def fetch_ashby():
    out = []
    for org in CONFIG["sources"]["ashby_orgs"]:
        try:
            data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
        except Exception as e:
            print(f"[ashby:{org}] failed: {e}"); continue
        for j in data.get("jobs", []):
            title = j.get("title", "")
            loc = j.get("location", "")
            desc = html.unescape(re.sub("<[^>]+>", " ", j.get("descriptionHtml", "")))
            flag = _ats_keep(title, desc)
            if flag is None: continue
            if not location_ok([loc]): continue
            out.append({"id": f"ashby:{org}:{j.get('jobUrl','')}", "title": title, "company": org.title(),
                        "location": loc or "n/a", "url": j.get("jobUrl", ""),
                        "source": "Ashby", "sponsorship": flag})
    return out


def gather():
    jobs = []
    if CONFIG["sources"]["simplify_github"]:
        jobs += fetch_simplify()
    jobs += fetch_greenhouse() + fetch_lever() + fetch_ashby()
    # de-dup within this run by id
    uniq = {j["id"]: j for j in jobs}
    return list(uniq.values())


# ---------- telegram ----------
def tg_send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[telegram] missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"); return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat, "text": text,
                          "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urlopen(req, timeout=20); return True
    except Exception as e:
        print(f"[telegram] send failed: {e}"); return False


def fmt(job):
    spark = {"Offers Sponsorship": "\u2705 sponsors",
             "Other": ""}.get(job["sponsorship"], "\u26a0\ufe0f " + job["sponsorship"])
    tag = f"  ({spark})" if spark else ""
    t = html.escape(job["title"]); c = html.escape(job["company"]); l = html.escape(job["location"])
    return f"\u2022 <b>{t}</b>\n  {c} \u2014 {l}{tag}\n  <a href=\"{job['url']}\">Apply</a>"


def send_batch(jobs):
    n = CONFIG["max_per_message"]
    for i in range(0, len(jobs), n):
        chunk = jobs[i:i+n]
        header = f"\U0001f6f0 <b>{len(jobs)} new match(es)</b>\n\n" if i == 0 else ""
        tg_send(header + "\n\n".join(fmt(j) for j in chunk))
        time.sleep(1)


def main():
    dry = "--dry-run" in sys.argv
    seen = load_seen()
    first_run = not os.path.exists(SEEN_PATH)
    jobs = gather()
    new = [j for j in jobs if j["id"] not in seen]
    print(f"fetched {len(jobs)} matching, {len(new)} new (first_run={first_run})")

    if dry:
        for j in new[:25]:
            print(f"  - {j['title']} | {j['company']} | {j['location']} | {j['sponsorship']}")
        print(f"... {len(new)} total new" if len(new) > 25 else "")
        return

    if first_run:
        # seed without flooding; one summary ping
        for j in jobs: seen.add(j["id"])
        save_seen(seen)
        tg_send(f"\U0001f6f0 <b>Internship Radar is live.</b>\nTracking {len(jobs)} matching roles. "
                f"You'll get a ping when new ones appear.")
        return

    if new:
        send_batch(new)
        for j in new: seen.add(j["id"])
        save_seen(seen)
    else:
        print("no new roles this run")


if __name__ == "__main__":
    main()
