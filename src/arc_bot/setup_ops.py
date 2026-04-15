from __future__ import annotations

import platform
import re
import shlex
import subprocess
import sys

from .config import (
    DEFAULT_CRON_SCHEDULE,
    LOCAL_ACCOUNTS_FILE,
    LOCAL_GMAIL_PASSES_FILE,
    LOCAL_PROXIES_FILE,
    LEGACY_ACCOUNTS_FILE,
    LEGACY_GMAIL_PASSES_FILE,
    LEGACY_LOCAL_ACCOUNTS_FILE,
    LEGACY_LOCAL_GMAIL_PASSES_FILE,
    LEGACY_LOCAL_PROXIES_FILE,
    LEGACY_PROXIES_FILE,
    LOG_DIR,
    SCRIPT_DIR,
    ConfigError,
    display_path,
    ensure_config_templates,
    read_non_comment_lines,
    resolve_config_file,
)

CRON_SCHEDULE_RE = re.compile(r"^[\d*/,\-]+(?:\s+[\d*/,\-]+){4}$")


def setup_environment() -> None:
    ensure_config_templates()

    steps: list[tuple[str, list[str]]] = [
        (
            "Install the project in editable mode",
            [sys.executable, "-m", "pip", "install", "--no-build-isolation", "-e", str(SCRIPT_DIR)],
        ),
        (
            "Install Chromium for Playwright",
            [sys.executable, "-m", "playwright", "install", "chromium"],
        ),
    ]
    if platform.system() == "Linux":
        steps.append(
            (
                "Install Linux browser dependencies for Chromium",
                [sys.executable, "-m", "playwright", "install-deps", "chromium"],
            )
        )

    print("=" * 72)
    print("Arc Bot setup")
    print("=" * 72)

    total_steps = len(steps) + 1
    for index, (description, command) in enumerate(steps, start=1):
        print(f"\n[{index}/{total_steps}] {description}")
        subprocess.run(command, check=True)

    print(f"\n[{total_steps}/{total_steps}] Review local configuration files")
    print_config_status()

    module_command = f"{shlex.quote(sys.executable)} -m arc_bot"
    legacy_command = f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_DIR / 'arc_daily.py'))}"
    print("\nNext steps:")
    print(f"  {module_command} --run-once")
    print(f"  {module_command} --daemon")
    print(f"  {legacy_command} --setup-cron")


def setup_cron(schedule: str = DEFAULT_CRON_SCHEDULE) -> None:
    validated_schedule = validate_cron_schedule(schedule)
    script_path = SCRIPT_DIR / "arc_daily.py"
    python_bin = shlex.quote(sys.executable)
    quoted_script = shlex.quote(str(script_path))
    quoted_log = shlex.quote(str(LOG_DIR / "arc_cron.log"))
    cron_command = f"cd {shlex.quote(str(SCRIPT_DIR))} && {python_bin} {quoted_script} --run-once >> {quoted_log} 2>&1"
    cron_entry = f"{validated_schedule} {cron_command}"
    cron_timezone = "CRON_TZ=Asia/Ho_Chi_Minh"

    print("=" * 72)
    print("Arc Bot cron setup")
    print("=" * 72)

    if platform.system() == "Windows":
        print("Windows does not use cron. Create a Task Scheduler job with this command:")
        print(f"  {sys.executable} {script_path} --run-once")
        return

    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing_crontab = result.stdout if result.returncode == 0 else ""
        filtered_lines = [
            line
            for line in existing_crontab.splitlines()
            if str(script_path) not in line
        ]
        filtered_lines = [line for line in filtered_lines if line.strip() != cron_timezone]
        filtered_lines.append(cron_timezone)
        filtered_lines.append(cron_entry)

        new_crontab = "\n".join(filtered_lines).rstrip("\n") + "\n"
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)

        print("Cron entry installed successfully.")
        print("Timezone : Asia/Ho_Chi_Minh")
        print(f"Schedule : {validated_schedule}")
        print(f"Command  : {cron_command}")
    except FileNotFoundError:
        print("crontab was not found on this system. Add the following command to your scheduler manually:")
        print(f"  {cron_timezone}")
        print(f"  {cron_entry}")


def validate_cron_schedule(schedule: str) -> str:
    normalized = " ".join(schedule.split())
    if not CRON_SCHEDULE_RE.fullmatch(normalized):
        raise ConfigError(
            "Invalid cron schedule. Use a standard five-field cron expression such as '11 7 * * *'."
        )
    return normalized


def print_config_status() -> None:
    files = [
        (
            LOCAL_ACCOUNTS_FILE,
            LEGACY_LOCAL_ACCOUNTS_FILE,
            LEGACY_ACCOUNTS_FILE,
            True,
            f"Add one Arc login email per line in {display_path(LOCAL_ACCOUNTS_FILE)}.",
        ),
        (
            LOCAL_GMAIL_PASSES_FILE,
            LEGACY_LOCAL_GMAIL_PASSES_FILE,
            LEGACY_GMAIL_PASSES_FILE,
            True,
            (
                "Add one Gmail app password per line in "
                f"{display_path(LOCAL_GMAIL_PASSES_FILE)}. "
                f"The order must match {display_path(LOCAL_ACCOUNTS_FILE)}."
            ),
        ),
        (
            LOCAL_PROXIES_FILE,
            LEGACY_LOCAL_PROXIES_FILE,
            LEGACY_PROXIES_FILE,
            False,
            (
                "Optional. Add one proxy per line in "
                f"{display_path(LOCAL_PROXIES_FILE)}, or leave the file blank to run direct connections."
            ),
        ),
    ]

    for preferred_path, legacy_local_path, legacy_default_path, required, hint in files:
        resolved_path = resolve_config_file(
            preferred_path,
            preferred_path.with_name(preferred_path.name.replace(".local", "")),
            legacy_local_path,
            legacy_default_path,
        )
        lines = read_non_comment_lines(resolved_path)
        if preferred_path == LOCAL_ACCOUNTS_FILE and any("----" in line for line in lines):
            print(f"  {display_path(resolved_path)}: invalid legacy format detected. {hint}")
            continue

        if lines:
            suffix = ""
            if resolved_path != preferred_path and resolved_path != preferred_path.with_name(preferred_path.name.replace(".local", "")):
                suffix = f" (legacy root file; preferred path: {display_path(preferred_path)})"
            print(
                "  "
                f"{display_path(resolved_path)}: {len(lines)} configured entr{'y' if len(lines) == 1 else 'ies'}"
                f"{suffix}"
            )
            continue

        if required:
            print(f"  {display_path(preferred_path)}: missing required content. {hint}")
        else:
            print(f"  {display_path(preferred_path)}: empty. {hint}")
