# Cosmic 11: going live (version 2)

Follow the steps in order. Each laptop session is one Codex prompt (see CODEX_PROMPTS.md) plus about 15 minutes in the browser.

## 0. Before you start (phone-friendly, do these first)
- [ ] Groq: console.groq.com -> Billing -> add a card -> switch to pay-as-you-go. Set a spending limit or alert.
      (Check Groq's current price per million tokens on their pricing page; this guide does not quote one.)
- [ ] GitHub account ready (harindermansh-stack). Private repo exists: vedic11rag.
- [ ] Render account: render.com -> sign up with GitHub.
- [ ] Decide the admin password for feedback export (a long phrase, at least 12 characters). Save it in your password manager.
- [ ] Beta tester list ready (20 to 30 people) and the invite message from your phone.

## 1. Laptop session A: install and test locally (about 45 minutes)
Prompt A in CODEX_PROMPTS.md. Test Money, Career, the Hindi button, the 1-year report. Send the results for review.

## 2. Laptop session B: push to GitHub (about 20 minutes)
Prompt B (private repo with the database) and Prompt C (clean PUBLIC code repo, AGPL). Never make the private repo public.

## 3. Render (about 15 minutes in the browser)
1. New + -> Web Service -> choose the private repo vedic11rag -> Language: Docker.
2. Instance type: Starter (always on). If the logs ever say "out of memory", switch to Standard.
3. Health check path: /health
4. Disks -> Add Disk: name cosmic-data, mount path /var/data, size 1 GB.
5. Environment variables (never put these in GitHub):

| Name | Value |
|---|---|
| GROQ_API_KEY | your Groq key |
| BETA_CODE | COSMIC11BETA |
| REPORT_MODE | beta |
| REPORT_CALL_GAP | 0 (after Groq pay-as-you-go is on; use 62 on the free plan) |
| GROQ_TPM_BUDGET | leave unset on the free plan; on pay-as-you-go set 30000 |
| DAILY_PER_USER | 10 |
| DAILY_TOTAL | 200 |
| REPORTS_PER_USER | 2 |
| REPORTS_TOTAL | 30 |
| LEGAL_OWNER | Harinder Singh |
| LEGAL_EMAIL | risebeyondkarma69@gmail.com |
| LEGAL_CITY | Bengaluru |
| LEGAL_UPDATED | October 2026 |
| SOURCE_URL | https://github.com/harindermansh-stack/cosmic11 |
| ADMIN_KEY | your long admin password |
| FEEDBACK_FILE | /var/data/feedback.jsonl |
| REPORT_DIR | /var/data/reports |
| USAGE_FILE | /var/data/usage.json |

6. Create Web Service. The first build takes about 10 to 15 minutes. Open the onrender.com address it gives you.

## 4. Smoke test on the live address (10 minutes)
- [ ] /health shows ok
- [ ] Home page loads; "Try an example" works
- [ ] Money question returns an answer with a timeline chart and sources
- [ ] Hindi button works
- [ ] 1-year report with the invite code completes (check it opens and Save as PDF works)
- [ ] /privacy and /terms show Harinder Singh and the email
- [ ] 👍/👎 box saves: open /api/feedback/export?key=YOUR_ADMIN_KEY and see the row
- [ ] A second report the same day from the same phone is refused politely (limits work)

## 5. Your own domain (optional, later)
Buy the domain (for example cosmicved11.com or cosmic11.in). In Render: Settings -> Custom Domains -> add it, then add the DNS record Render shows. HTTPS is automatic. Update the invite message link.

## 6. Daily routine during the beta (5 minutes)
- Download feedback: /api/feedback/export?key=YOUR_ADMIN_KEY (opens as a spreadsheet).
- Check Groq usage and Render logs for errors.
- Send fixes to Claude as one list; they are bundled into one release.

## 7. If something breaks
Render -> your service -> Events -> Rollback to the previous deploy. The database is baked into the image, so a rollback restores everything except feedback, reports and limits (those live on the disk).

## 8. Licence checklist (AGPL)
- [x] Public code repo (Prompt C), with LICENSE and NOTICE
- [x] SOURCE_URL set, so the footer's "Source code" link works
- [x] Books, database, .env and keys are never in the public repo
- [ ] Revisit the Swiss Ephemeris professional licence only if paid revenue becomes steady
