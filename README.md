Keeps your Notion Task Tracker filled in automatically. Runs at 1am Eastern, creates the recurring tasks that are due, and clears the finished day away.

Free to run. No server. Roughly 15 minutes to set up, once.

What it does each night
Reads the rules in the 🔁 Recurring Rules tab
Makes sure a dated, unchecked task exists for every occurrence in the next 60 days
Clears yesterday: anything you finished or explicitly skipped is archived, unless you flagged it as an Accomplishment

Archived pages go to Notion's trash, where they're recoverable for 30 days. Nothing unfinished is ever archived while it's still the most recent copy.

The three checkboxes
Column	What it means	What the 1am run does with it
Done	You did it	Archives it the next night
Won't Do	Skipping this one	Archives it the next night. The next week's / month's copy still appears
Accomplishment	Worth remembering	Keeps it forever and shows it in 🏆 Accomplishments

Checking Won't Do on a Weekly task only skips that occurrence. Each copy carries its own date and is created independently from the rule, so a chore you skip this Thursday still shows up next Thursday.

Today's checkmarks stay visible all day — cleanup only touches dates before today, so the day still reads as a day you got through. They clear at 1am.

Accomplishment beats everything. Flag it and it survives, whether it's marked Done, Won't Do, or neither.

Setup
1. Make a Notion integration
Go to notion.so/my-integrations → New integration
Name it anything ("Task Tracker sync")
Pick your workspace, then Submit
Copy the Internal Integration Secret — a long string starting ntn_

Keep this private. It can read and write your workspace.

2. Give it access to the database
Open your Task Tracker page in Notion
Click ••• (top right) → Connections → Connect to
Pick the integration you just made

Without this step the script gets a 404 — Notion integrations only see what they've been explicitly connected to.

3. Put the code on GitHub
Create a new private repository
Upload sync_tasks.py and the .github/workflows/daily-sync.yml file (keep the folder structure — the workflow must live at .github/workflows/daily-sync.yml)
4. Add your token as a secret

In the repo: Settings → Secrets and variables → Actions → New repository secret

Name: NOTION_TOKEN
Value: the secret from step 1

Never paste the token into the script itself.

5. Test it

Go to the Actions tab → Task Tracker daily sync → Run workflow.

Click into the run to watch the log. You should see lines like:

Task Tracker sync - 2026-08-26 (America/New_York)
  + Grocery shop -> 2026-10-24
  - cleared: Stretch for 15 min (2026-08-25)
  - superseded: Do laundry (2026-08-24)
Done. 6 created, 4 cleared, 1 superseded.

Three kinds of line, so you can tell at a glance what happened:

+ — a new dated task was created from a rule
cleared — settled (Done or Won't Do) and older than today
superseded — an unfinished chore that had a newer copy, so the old one went
Adding a new recurring task later

Open the 🔁 Recurring Rules tab and add a row:

Field	What to put
Name	The task name
Is Rule	✅ checked
Type	Daily / Weekly / Monthly / Quarterly
Time Block	Morning / Afternoon / After Dinner
Recurrence	see patterns below
Rule Key	any short unique label, e.g. water-plants
Date	leave empty
Recurrence patterns
Pattern	Means
daily	every day
weekly:MO	every Monday (MO TU WE TH FR SA SU)
monthly:2SA	2nd Saturday of every month
monthly:-1SU	last Sunday of every month
quarterly:1SA	1st Saturday of Jan / Apr / Jul / Oct

The task appears the next morning. To see it immediately, run the workflow by hand from the Actions tab.

Rule Key must be unique. It's how the script knows which tasks came from which rule. Reusing one will make two rules fight over the same tasks.

Changing or removing a rule
Change a day or time block: edit the rule. Tasks already created keep the old values — adjust or delete those by hand if it matters.
Stop a habit: uncheck Is Rule, or delete the rule row. Already-created future copies stay; delete them in the Calendar tab if you don't want them.
Things worth knowing

Why 1am works out. GitHub runs cron in UTC and doesn't follow daylight saving, so 0 5 * * * is 1am Eastern in summer and midnight in winter. The script resolves "today" against America/New_York itself rather than trusting the runner's clock, so the hour it fires can drift without it ever disagreeing with your calendar about which day it is.

The 60-day window. Tasks are created two months ahead so the Calendar looks real. Change HORIZON_DAYS at the top of the script if you'd rather see more or less.

Cleared tasks can't come back. Occurrences are only ever generated from today forward, so archiving yesterday is safe — the next run has no way to recreate a past date.

Quarterly means Jan/Apr/Jul/Oct. If your quarters start elsewhere, adjust the month list in the occurrences function.

5th occurrences are skipped. monthly:5SA produces nothing in months without a 5th Saturday. Use monthly:-1SA for "last Saturday" instead.

If a run fails, open the Actions tab and read the log — the script prints what it was doing when it stopped. A 404 usually means step 2 was missed. A 401 means the token is wrong or expired.

GitHub pauses schedules on inactive repos. If there's no activity for ~60 days, GitHub disables the cron. You'll get an email; clicking to re-enable is enough.

Testing changes

test_cleanup.py runs the archiving rules against fake data with the API stubbed out, so it never touches Notion:
