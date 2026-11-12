---
name: "linkedin-target-scout"
description: "Find the 3 people most likely to get a resume in front of the right person for one job: the recruiter for the req, the hiring manager, and the skip-level director. Use when the user runs /linkedin-target-scout JDx (reads context from their local ATS Pipeline app at localhost:5001 and imports results back into its Stage 10 page) or gives a job posting URL. Read-only LinkedIn browsing in the user's own session. Does not draft or send outreach.\ntools:\n  - browser\n  - linkedin"
---

# Purpose

For one job, identify the people who can **route the resume to the person making the hiring
decision**, then hand them back to the user's ATS Pipeline app (Stage 10).

Goal: interview call. The best target owns or controls this req, not someone with a similar title.

Do not:
- Draft outreach messages unless explicitly asked
- Click Connect, Follow, Message, InMail, or anything that sends or notifies
- Recommend applying, or judge people
- Collect emails, phone numbers, or profile content beyond the output fields

# Input

Either:
- `JDx` (for example `/linkedin-target-scout JD7`): use the app flow below
- A job posting URL: skip steps 1 and 5, return the output to the user in chat

If the user gives several JDs, do at most 3 per sitting and say so.

# Step 1: Read context from the app

Open `http://localhost:5001/api/scout-context/JDx` in the browser and read the JSON.

Use: `company`, `role_title`, `job_id`, `job_url`, `locations`, `rules`, `output_contract`, `import_page`.

- If the page does not load: tell the user the app is not reachable at localhost:5001 and ask for the job URL instead.
- If `gated_in` is `false`: stop. Tell the user the reason in `gate_reason` and ask whether to continue anyway.
- If `company` is null (listed in `missing_meta`): ask the user for the company before searching.

# Step 2: Find the LinkedIn version of the job

Search LinkedIn Jobs for `role_title` + `company` (+ `job_id` if present). If found, read its
**"Meet the hiring team"** section and note who posted it. These are the strongest recruiter and
hiring-manager signals available; prefer them over search guesses.

# Step 3: Find people, in this priority order

1. **recruiter**: the recruiter or talent partner for THIS req. Sources, strongest first: "Meet the
   hiring team", the person who posted the job, then a talent partner or technical recruiter at
   `company` covering this function and location.
2. **hiring_manager**: the manager who would own this hire, meaning they manage the team or function in
   the JD (titles like Senior Manager, Manager, Group Manager, or Director of that function).
   **A peer at the same level as the role is NOT a hiring manager.**
3. **skip_level**: the director over that org.
4. **referrer** (fallback only): a peer on the team. Use only when a slot above cannot be filled, and
   never label a peer as recruiter or hiring manager.

Use LinkedIn people search with the **Current company** filter set to `company`, plus location.

# Step 4: Verify every candidate

- **Current employer:** open the profile and check the top Experience entry. Headlines go stale; a
  real run found a "Qualcomm" headline on someone who had left 7 months earlier.
- **Location:** prefer `locations`.
- **Activity:** check the profile's Activity section. Prefer activity in the last 30 days. If not
  verified, say "not verified" rather than guessing.
- **Pace:** about 15 profile views per JD. Stop immediately and tell the user on any CAPTCHA,
  checkpoint, or unusual-activity warning. Never try to get past one.

If the hiring manager cannot be identified, say **"Hiring manager not publicly identifiable"**, set
`hiring_manager_identifiable` to false, leave that slot out, and fill skip_level plus one referrer.

# Output

First, a short readable summary in chat: one line per person (slot, name, title, why), plus anything
uncertain.

Then exactly one JSON block matching `output_contract`:

```json
{
  "jd": "JD7",
  "hiring_manager_identifiable": true,
  "notes": "one or two sentences on anything uncertain",
  "targets": [
    {
      "slot": "recruiter",
      "name": "",
      "title": "",
      "linkedin_url": "https://www.linkedin.com/in/...",
      "why_relevant": "one sentence",
      "recent_activity": "one sentence, or 'not verified'",
      "warm_path": "shared employer or school with the user, if visible, else empty",
      "verified_current_employer": true
    }
  ]
}
```

Rules for the JSON:
- `slot` is one of `recruiter`, `hiring_manager`, `skip_level`, `referrer`
- At most one person per core slot; at most 2 referrers
- `linkedin_url` is the `https://www.linkedin.com/in/...` profile URL only, no extra text

# Step 5: Import into the app (ask first)

1. Open `import_page` (for example `http://localhost:5001/network/JD7`).
2. Paste the JSON block into the **Import scout results** box.
3. Show the user the names you are about to import and **ask for confirmation**.
4. Only after they say yes, click **Import**.
5. Report what the page says (imported names and any warnings). The app will not overwrite a slot
   where outreach has already started; mention any such warning.

# Success criteria

- A recruiter found for the req, or an explicit statement that none was identifiable
- Hiring manager is a manager of the function, not a peer
- Every person verified as a current employee from their Experience section
- Recent activity stated honestly
- Results imported into the app only after the user confirmed
