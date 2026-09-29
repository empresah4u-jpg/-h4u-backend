"""Generate a validated plan from the read-only hotel preview, never apply RAW."""
import argparse
import json
from pathlib import Path
from app.db import get_connection
from pipelines.ingestion_plan import create


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    args=parser.parse_args()
    with get_connection() as conn:
        plan=create(conn,json.loads(args.input.read_text()))
    print(json.dumps(plan,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
