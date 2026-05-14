"""
HomeCartel Actual Photo Automation — unified launcher.

Runs both pipeline stages (stage1-find-actual-photo and stage2-supplier-bot)
from a single command. One Ctrl+C stops both.

Examples:
    python app.py                  # run both stages (default)
    python app.py --stage stage1   # run stage 1 only
    python app.py --stage stage2   # run stage 2 only
    python app.py --dry-run        # both stages: log actions without
                                   # uploading files, sending Telegram, or
                                   # updating Zoho records
    python app.py --once           # one-shot: stage 1 processes one record
                                   # and exits; stage 2 polls once, waits
                                   # briefly for Telegram replies, then exits
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGE1_DIR = ROOT / "stage1-find-actual-photo"
STAGE2_DIR = ROOT / "stage2-supplier-bot"


def _stage1_cmd(dry_run: bool, once: bool) -> list[str]:
    """Build the Stage 1 (actual_photo_automation) command line."""
    cmd: list[str] = [sys.executable, "-m", "actual_photo_automation"]
    if dry_run:
        cmd.append("--dry-run")
    if once:
        cmd.append("--test-one")
    return cmd


def _stage2_cmd(dry_run: bool, once: bool) -> list[str]:
    """Build the Stage 2 (Telegram supplier bot) command line."""
    cmd: list[str] = [sys.executable, "app.py"]
    if dry_run:
        cmd.append("--dry-run")
    if once:
        cmd.append("--once")
    return cmd


def _launch(label: str, cmd: list[str], cwd: Path) -> subprocess.Popen:
    print(f"[launcher] starting {label}: {' '.join(cmd)}  (cwd={cwd})")
    return subprocess.Popen(cmd, cwd=cwd)


def _terminate_all(procs: list[tuple[str, subprocess.Popen]]) -> None:
    for label, proc in procs:
        if proc.poll() is None:
            print(f"[launcher] terminating {label}…")
            proc.terminate()
    deadline = time.monotonic() + 10
    for label, proc in procs:
        timeout = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"[launcher] {label} did not exit cleanly; killing.")
            proc.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python app.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--stage",
        choices=("both", "stage1", "stage2"),
        default="both",
        help="Which stage to run (default: both).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Both stages: log actions without uploading files, sending "
            "Telegram messages, or updating Zoho records."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "One-shot: stage 1 processes a single eligible record and "
            "exits; stage 2 polls once, waits briefly for Telegram "
            "replies, then exits. Useful for testing."
        ),
    )
    args = parser.parse_args(argv)

    procs: list[tuple[str, subprocess.Popen]] = []

    if args.stage in ("both", "stage1"):
        procs.append((
            "stage1",
            _launch("stage1", _stage1_cmd(args.dry_run, args.once), STAGE1_DIR),
        ))

    if args.stage in ("both", "stage2"):
        procs.append((
            "stage2",
            _launch("stage2", _stage2_cmd(args.dry_run, args.once), STAGE2_DIR),
        ))

    def _handle_signal(signum, _frame):
        print(f"\n[launcher] received signal {signum}; shutting down…")
        _terminate_all(procs)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Block until any child exits, then shut the rest down so the user
    # gets a clean exit when --once / --test-one wraps up. Returns the
    # first non-zero exit code if any, else 0.
    first_exit: tuple[str, int] | None = None
    while True:
        for label, proc in procs:
            ret = proc.poll()
            if ret is not None and first_exit is None:
                first_exit = (label, ret)
                print(f"[launcher] {label} exited with code {ret}")
        if first_exit is not None:
            break
        time.sleep(1)

    _terminate_all(procs)
    return first_exit[1] or 0


if __name__ == "__main__":
    sys.exit(main())
