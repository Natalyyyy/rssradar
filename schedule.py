"""Расписание rssradar: launchd (Mac) или cron (Linux)."""
import argparse
import platform
import subprocess
import sys
from pathlib import Path

import common

LABEL_PULSE = "com.rssradar.pulse"
LABEL_WEEKLY = "com.rssradar.weekly"
CRON_MARK = "# rssradar"
WEEKDAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def parse_time(value):
    hour, minute = value.split(":")
    return int(hour), int(minute)


def _python():
    return sys.executable or "python3"


def plist_xml(label, mode, hour, minute, weekday=None):
    weekday_xml = ("<key>Weekday</key><integer>{}</integer>".format(weekday)
                   if weekday is not None else "")
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>{python}</string>
    <string>{report}</string>
    <string>{mode}</string>
    <string>--send</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>{minute}</integer>
    {weekday_xml}
  </dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{errlog}</string>
</dict></plist>
""".format(label=label, python=_python(), report=common.ROOT / "report.py",
           mode=mode, hour=hour, minute=minute, weekday_xml=weekday_xml,
           log=common.DATA_DIR / (mode + ".log"),
           errlog=common.DATA_DIR / (mode + ".err.log"))


def cron_lines(cfg):
    ph, pm = parse_time(cfg["pulse_time"])
    wh, wm = parse_time(cfg["weekly_time"])
    wd = WEEKDAYS[cfg["weekly_day"]]
    report = common.ROOT / "report.py"
    return [
        "{} {} * * * {} {} pulse --send {}".format(pm, ph, _python(), report, CRON_MARK),
        "{} {} * * {} {} {} weekly --send {}".format(wm, wh, wd, _python(), report, CRON_MARK),
    ]


def install(cfg):
    common.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Darwin":
        agents = Path.home() / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        ph, pm = parse_time(cfg["pulse_time"])
        wh, wm = parse_time(cfg["weekly_time"])
        jobs = [(LABEL_PULSE, "pulse", ph, pm, None),
                (LABEL_WEEKLY, "weekly", wh, wm, WEEKDAYS[cfg["weekly_day"]])]
        for label, mode, h, m, wd in jobs:
            path = agents / (label + ".plist")
            path.write_text(plist_xml(label, mode, h, m, wd), encoding="utf-8")
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
            subprocess.run(["launchctl", "load", str(path)], check=True)
        print("Готово (launchd): пульс ежедневно в {}, отчёт по {} в {}.".format(
            cfg["pulse_time"], cfg["weekly_day"], cfg["weekly_time"]))
    else:
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        lines = [l for l in (current.stdout or "").splitlines() if CRON_MARK not in l]
        lines += cron_lines(cfg)
        subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                       text=True, check=True)
        print("Готово (cron): пульс ежедневно, отчёт еженедельно.")


def remove():
    if platform.system() == "Darwin":
        agents = Path.home() / "Library" / "LaunchAgents"
        for label in (LABEL_PULSE, LABEL_WEEKLY):
            path = agents / (label + ".plist")
            if path.exists():
                subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
                path.unlink()
        print("Расписание снято (launchd).")
    else:
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        lines = [l for l in (current.stdout or "").splitlines() if CRON_MARK not in l]
        subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                       text=True, check=True)
        print("Расписание снято (cron).")


def status():
    if platform.system() == "Darwin":
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
        found = [l for l in out.splitlines() if "rssradar" in l]
    else:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        found = [l for l in out.splitlines() if CRON_MARK in l]
    print("\n".join(found) if found else "Расписание не установлено.")


def main():
    ap = argparse.ArgumentParser(description="Расписание rssradar")
    ap.add_argument("action", choices=["install", "remove", "status"])
    args = ap.parse_args()
    if args.action == "install":
        try:
            cfg = common.load_config()
        except common.ConfigError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        install(cfg)
    elif args.action == "remove":
        remove()
    else:
        status()


if __name__ == "__main__":
    main()
