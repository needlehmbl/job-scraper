"""
Scrape-only pipeline: scrape -> filter -> dedupe (within run + vs Postgres)
-> Postgres.

    python main.py            # store new leads in Postgres

Then open the dashboard and review the NEW rows. Mark rows REVIEWED /
APPLIED as you go; existing rows are never re-added on later runs.
"""
from pipeline import run_scrape


def main():
    result = run_scrape()
    print(result["summary"])


if __name__ == "__main__":
    main()