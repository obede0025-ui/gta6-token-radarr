
name: GTA6 Learning Collector

on:
  schedule:
    - cron: '*/5 * * * *'
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: gta6-learning-db
  cancel-in-progress: false

jobs:
  collect:
    runs-on: ubuntu-latest
    timeout-minutes: 8
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install "requests>=2.31"

      - name: Scan and label
        run: python collector.py

      - name: Save data
        run: |
          git config user.name 'github-actions[bot]'
          git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
          git add data/learning.db data/latest_scan.json
          if ! git diff --cached --quiet; then
            git commit -m 'data: gta6 learning snapshots [skip ci]'
            git push
          fi
