import argparse
import uuid

from app.config import get_settings
from app.database import SessionLocal
from app.models.schema import AuditRun, FetchRun
from app.services.jobs import execute_audit, execute_fetch


def main() -> None:
    parser = argparse.ArgumentParser(description="Bama SaaS operational commands")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--max-ads", type=int)
    sub.add_parser("audit")
    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal.begin() as db:
        if args.command == "fetch":
            run = FetchRun(max_ads=args.max_ads or settings.bama_max_ads, page_pause=settings.bama_page_pause)
        else:
            run = AuditRun()
        db.add(run)
        db.flush()
        run_id: uuid.UUID = run.id
    (execute_fetch if args.command == "fetch" else execute_audit)(run_id)
    print(run_id)


if __name__ == "__main__":
    main()
