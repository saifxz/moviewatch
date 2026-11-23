from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .browser import open_bookmyshow_login
from .config import Config, config_path, logs_dir, project_dir
from .installer import DEFAULT_TASK, install_task, query_task, uninstall_task
from .notifier import TelegramNotifier
from .runner import run_forever, run_once

logger = logging.getLogger("movie_watch")


def setup_logging(verbose: bool = False) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    # httpx logs full request URLs, including the Telegram bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if root.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    log_dir = logs_dir()
    file_handler = RotatingFileHandler(
        log_dir / "movie-watch.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def bootstrap() -> None:
    env_file = project_dir() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)


def load_config(path: str | Path | None) -> Config:
    target = Path(path).expanduser().resolve() if path else config_path()
    return Config.load(target)


def cmd_run(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    config = load_config(args.config)
    dry_run = args.dry_run or os.getenv("MOVIE_WATCH_DRY_RUN", "").lower() in {"1", "true", "yes"}
    if args.once:
        summary = run_once(config, dry_run=dry_run)
        print(
            f"scanned={len(summary.scanned)} new={len(summary.new_shows)} "
            f"alerts={summary.alerts_sent} failures={len(summary.failures)}"
        )
        return 1 if summary.failures and not summary.scanned else 0
    from .api import start_control_server

    start_control_server()
    run_forever(config, dry_run=dry_run)
    return 0


def cmd_init(_: argparse.Namespace) -> int:
    root = project_dir()
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    cfg = root / "config.yaml"
    if not cfg.exists():
        example = root / "config.example.yaml"
        if example.exists():
            shutil.copyfile(example, cfg)
            print(f"created {cfg}")
        else:
            print("config.example.yaml missing; create config.yaml manually")
    else:
        print(f"config already exists: {cfg}")

    env = root / ".env"
    if not env.exists():
        example_env = root / ".env.example"
        if example_env.exists():
            shutil.copyfile(example_env, env)
            print(f"created {env}")
    print("\nNext: fill config.yaml and .env, then run `movie-watch check --dry-run`.")
    return 0


def cmd_install_task(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    config = load_config(args.config)
    path = Path(args.config).expanduser().resolve() if args.config else config_path()
    interval = config.poll_seconds if config.poll_seconds > 0 else config.poll_minutes * 60
    output = install_task(path, interval, args.task)
    print(output or f"installed task {args.task}")
    print(f"Runs every {interval} second(s) under the current Windows user.")
    return 0


def cmd_uninstall_task(args: argparse.Namespace) -> int:
    output = uninstall_task(args.task)
    print(output or f"removed task {args.task}")
    return 0


def cmd_task_status(args: argparse.Namespace) -> int:
    try:
        print(query_task(args.task))
    except RuntimeError as exc:
        print(f"task not found or query failed: {exc}")
        return 1
    return 0


def cmd_test_telegram(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    notifier = TelegramNotifier(dry_run=args.dry_run, outbox=project_dir() / "data" / "alerts.jsonl")
    ok = notifier.send_text("🎬 Movie watch test message. If you see this, Telegram is configured.")
    if not ok and notifier.enabled and not args.dry_run:
        return 1
    if not notifier.enabled and not args.dry_run:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first.")
        return 1
    return 0


def cmd_browser_check(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    config = load_config(args.config)
    matches = [target for target in config.targets if target.name == args.target]
    if not matches:
        available = ", ".join(target.name for target in config.targets) or "(none)"
        print(f"target '{args.target}' not found. Available: {available}")
        return 1
    open_bookmyshow_login(config, matches[0])
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    from .api import serve

    serve()
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="path to config.yaml")
    common.add_argument(
        "--verbose", "-v", action="store_true", default=argparse.SUPPRESS, help="debug logging"
    )
    common.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print/write alerts without sending Telegram",
    )

    parser = argparse.ArgumentParser(
        prog="movie-watch",
        parents=[common],
        description="Alert on new movie shows at a chosen theatre (read-only watcher).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", parents=[common], help="scan now (default) or loop continuously")
    run.add_argument("--once", action="store_true", help="scan once and exit")
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", parents=[common], help="alias for `run --once`")
    check.set_defaults(func=cmd_run, once=True)

    init = sub.add_parser("init", parents=[common], help="create config.yaml and .env from examples")
    init.set_defaults(func=cmd_init)

    install = sub.add_parser(
        "install-task", parents=[common], help="install Windows Task Scheduler entry"
    )
    install.add_argument("--task", default=DEFAULT_TASK)
    install.set_defaults(func=cmd_install_task)

    uninstall = sub.add_parser(
        "uninstall-task", parents=[common], help="remove Windows Task Scheduler entry"
    )
    uninstall.add_argument("--task", default=DEFAULT_TASK)
    uninstall.set_defaults(func=cmd_uninstall_task)

    status = sub.add_parser("task-status", parents=[common], help="show scheduled task status")
    status.add_argument("--task", default=DEFAULT_TASK)
    status.set_defaults(func=cmd_task_status)

    test = sub.add_parser(
        "test-telegram", parents=[common], help="send a test Telegram message"
    )
    test.set_defaults(func=cmd_test_telegram)

    browser = sub.add_parser(
        "browser-check",
        parents=[common],
        help="open BookMyShow in a visible browser to finish Cloudflare/login",
    )
    browser.add_argument("target", help="target name from config.yaml")
    browser.set_defaults(func=cmd_browser_check)

    serve_parser = sub.add_parser(
        "serve", parents=[common], help="run the one-shot scan HTTP API"
    )
    serve_parser.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    bootstrap()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "config"):
        args.config = None
    if not hasattr(args, "verbose"):
        args.verbose = False
    if not hasattr(args, "dry_run"):
        args.dry_run = False
    if not getattr(args, "command", None):
        args.once = False
        return cmd_run(args)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
