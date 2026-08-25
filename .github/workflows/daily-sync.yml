name: Task Tracker daily sync
 
on:
  schedule:
    # 05:00 UTC = 1am Eastern Daylight Time (midnight during winter, EST).
    # GitHub runs cron in UTC and doesn't follow daylight saving, so the
    # wall-clock time drifts by an hour in November. That's cosmetic here:
    # the script resolves "today" against America/New_York itself, so it
    # always agrees with your calendar regardless of when it fires.
    - cron: "0 5 * * *"
 
  # Lets you trigger a run by hand from the Actions tab.
  workflow_dispatch:
 
jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
 
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
 
      - name: Sync tasks
        env:
          NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
        run: python sync_tasks.py
 
Notifications are turned off for Claude. Enable them in System Settings to get alerts when Claude finishes a task.
