import argparse
from urllib.parse import urlencode

from .config import Settings
from .db import create_session_factory, migrate
from .jobs import classify_requests, cleanup_old_audit_data
from .reports import (
    save_daily_report_snapshot,
    suspicious_report,
    token_usage_report,
    update_daily_report_wecom_result,
    wecom_daily_summary,
)
from .timeutil import fmt_local, parse_range
from .wecom import push_wecom_text, push_wecom_textcard
from .work_summary import summarize_user_work


def main() -> None:
    parser = argparse.ArgumentParser(description="Token audit service utility")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    for name in ["classify", "summarize-work", "report", "suspicious", "save-report", "push-wecom", "cleanup"]:
        p = sub.add_parser(name)
        if name != "cleanup":
            p.add_argument("--start")
            p.add_argument("--end")
        if name in {"classify", "summarize-work"}:
            p.add_argument("--force", action="store_true")

    args = parser.parse_args()
    settings = Settings.from_env()
    settings.validate_runtime()
    session_factory = create_session_factory(settings.database_url)
    migrate(session_factory)

    if args.command == "migrate":
        print("migrated")
        return
    if args.command == "cleanup":
        with session_factory() as session:
            result = cleanup_old_audit_data(session, settings)
            session.commit()
            print(f"cleanup={result}")
        return

    start_dt, end_dt = parse_range(args.start, args.end, settings.timezone)
    with session_factory() as session:
        if args.command == "classify":
            count = classify_requests(session, settings, start_dt, end_dt, force=args.force)
            session.commit()
            print(f"classified={count}")
        elif args.command == "summarize-work":
            count = summarize_user_work(session, settings, start_dt, end_dt, force=args.force)
            session.commit()
            print(f"summarized_users={count}")
        elif args.command == "report":
            print(token_usage_report(session, start_dt, end_dt, settings.timezone))
        elif args.command == "suspicious":
            print(suspicious_report(session, start_dt, end_dt, settings.timezone))
        elif args.command == "save-report":
            detail_url = _daily_report_url(settings, start_dt, end_dt)
            row = save_daily_report_snapshot(session, start_dt, end_dt, settings.timezone, detail_url)
            session.commit()
            print(f"daily_report_id={row.id}")
        elif args.command == "push-wecom":
            content = token_usage_report(session, start_dt, end_dt, settings.timezone)
            content += "\n\n" + suspicious_report(session, start_dt, end_dt, settings.timezone)
            detail_url = _daily_report_url(settings, start_dt, end_dt)
            daily_report = save_daily_report_snapshot(session, start_dt, end_dt, settings.timezone, detail_url)
            session.commit()
            if detail_url:
                title, description = wecom_daily_summary(session, start_dt, end_dt, settings.timezone, detail_url)
                result = push_wecom_textcard(settings, title, description, detail_url)
            else:
                result = push_wecom_text(settings, content)
            update_daily_report_wecom_result(session, daily_report, result)
            session.commit()
            print(result)


def _daily_report_url(settings: Settings, start, end) -> str:
    if not settings.public_base_url or not settings.report_access_token:
        return ""
    start_date = fmt_local(start, settings.timezone)[:10]
    end_date = fmt_local(end, settings.timezone)[:10]
    query = {"start": start_date, "end": end_date, "token": settings.report_access_token}
    if start_date == end_date:
        query = {"date": start_date, "token": settings.report_access_token}
    return f"{settings.public_base_url}/reports/daily?{urlencode(query)}"


if __name__ == "__main__":
    main()
