#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Graype
"""Traffic accounting reports.

Daily reports are selected by local calendar-day offset:
  daily       -> today
  daily -1    -> yesterday
  ...
  daily -14   -> fourteen days ago

Groups come from the devices themselves. In /etc/config/r3s-accounting,
``report_hourly_groups`` (default ``Kids``) get a per-device 24-hour grid and
``report_device_groups`` (default ``IoT``) get a per-device list; every other
group gets a one-line summary. Device names never decide group membership.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from datetime import datetime, timedelta

DB_DEFAULT = "/var/lib/r3s-accounting/accounting.sqlite3"
BAR_WIDTH = 20
TELEGRAM_SPARK_CHARS = "▁▂▃▄▅▆▇█"
MARKDOWN_V2_SPECIALS = re.compile(r"([_\\*\[\]\(\)~`>#+\-=|{}.!\\\\])")
DEVICE_MARKERS = ("🔵", "🟢", "🟠", "🟣", "🔴", "🟡", "⚪", "🟤")
ACCOUNTING_UCI = "/etc/config/r3s-accounting"


def uci_list(name: str, default: list[str]) -> list[str]:
    """Space-separated option from /etc/config/r3s-accounting (default if unset)."""
    try:
        text = open(ACCOUNTING_UCI, encoding="utf-8").read()
    except OSError:
        return default
    match = re.search(rf"option\s+{re.escape(name)}\s+['\"]?([^'\"\n]*)", text)
    values = match.group(1).split() if match else []
    return values or default


def report_groups(conn: sqlite3.Connection) -> tuple[list[str], list[str], list[str]]:
    """(hourly-grid groups, summary groups, per-device groups) for reports."""
    present = [str(r["group_name"]) for r in rows(
        conn, "SELECT DISTINCT group_name FROM devices WHERE enabled=1")]
    order = uci_list("groups", ["Personal", "Kids", "IoT"])
    present.sort(key=lambda g: (order.index(g) if g in order else len(order), g))
    hourly = [g for g in uci_list("report_hourly_groups", ["Kids"]) if g in present]
    detail = [g for g in uci_list("report_device_groups", ["IoT"]) if g in present]
    return hourly, [g for g in present if g not in hourly], detail


def fmt_bytes(value: int) -> str:
    value = float(max(0, value))
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while value >= 1000 and i < len(units) - 1:
        value /= 1000
        i += 1
    if i == 0: return f"{value:.0f} B"
    if value >= 100: return f"{value:.0f} {units[i]}"
    if value >= 10: return f"{value:.1f} {units[i]}"
    return f"{value:.2f} {units[i]}"


def markdown_v2(value: object) -> str:
    """Escape dynamic text for Telegram's MarkdownV2 parser."""
    return MARKDOWN_V2_SPECIALS.sub(r"\\\1", str(value))


def bar(value: int, maximum: int, width: int = BAR_WIDTH) -> str:
    if value <= 0 or maximum <= 0: return ""
    return "█" * max(1, min(width, round(value * width / maximum)))


def telegram_sparkline(values: list[int | None], maximum: int) -> str:
    if maximum <= 0:
        return "·" * len(values)
    output = []
    for value in values:
        if value is None:
            output.append("·")
            continue
        level = max(0, min(len(TELEGRAM_SPARK_CHARS) - 1, round(value * 7 / maximum)))
        output.append(TELEGRAM_SPARK_CHARS[level])
    return "".join(output)


def telegram_bucket_values(values: list[int | None], bucket_size: int = 2) -> list[int | None]:
    """Average consecutive hours so Telegram sparklines remain compact on mobile."""
    buckets: list[int | None] = []
    for start in range(0, len(values), bucket_size):
        bucket = [value for value in values[start:start + bucket_size] if value is not None]
        buckets.append(round(sum(bucket) / len(bucket)) if bucket else None)
    return buckets


def rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, args).fetchall()


def report_date(offset: int) -> str:
    return (datetime.now().date() + timedelta(days=offset)).isoformat()


def effective_daily(conn: sqlite3.Connection, date: str) -> list[sqlite3.Row]:
    return rows(conn, """
        SELECT d.device_id,d.name,d.group_name,SUM(rx_bytes) AS rx,SUM(tx_bytes) AS tx,
               SUM(rx_bytes+tx_bytes) AS total
        FROM usage_daily u JOIN devices d ON d.device_id=u.device_id
        WHERE u.local_date=? AND d.enabled=1
        GROUP BY d.device_id,d.name,d.group_name ORDER BY total DESC,d.device_id
    """, (date,))


def unresolved_daily(conn: sqlite3.Connection, date: str) -> list[sqlite3.Row]:
    return rows(conn, """
        SELECT uu.ip,COALESCE(SUM(uu.rx_bytes),0) AS rx,COALESCE(SUM(uu.tx_bytes),0) AS tx,
               COALESCE(SUM(uu.rx_bytes+uu.tx_bytes),0) AS total
        FROM unresolved_usage uu
        WHERE uu.local_date=? GROUP BY uu.ip ORDER BY total DESC,uu.ip
    """, (date,))


def daily_totals(conn: sqlite3.Connection, date: str):
    known=effective_daily(conn,date); unresolved=unresolved_daily(conn,date)
    kt=sum(int(r["total"]) for r in known); ut=sum(int(r["total"]) for r in unresolved)
    return known,unresolved,kt,ut,kt+ut


def print_status(known_rx: int, known_tx: int, unresolved_rx: int, unresolved_tx: int, unresolved_count: int) -> None:
    known_total = known_rx + known_tx
    unresolved_total = unresolved_rx + unresolved_tx
    observed_total = known_total + unresolved_total
    print("STATUS")
    print("────────────────────────────────────────────────")
    print(f"House download     {fmt_bytes(known_rx + unresolved_rx):>10}")
    print(f"House upload       {fmt_bytes(known_tx + unresolved_tx):>10}")
    print(f"House total        {fmt_bytes(observed_total):>10}")
    print(f"Known download     {fmt_bytes(known_rx):>10}")
    print(f"Known upload       {fmt_bytes(known_tx):>10}")
    print(f"Known total        {fmt_bytes(known_total):>10}")
    print(f"Unresolved         {fmt_bytes(unresolved_total):>10}")
    print(f"Unresolved clients {unresolved_count:>10}")
    print("Quota alerts       None")
    print()


def print_hourly_grid(conn: sqlite3.Connection, date: str, group: str) -> None:
    selected = rows(
        conn,
        """SELECT device_id,name FROM devices
           WHERE enabled=1 AND group_name=?
           ORDER BY name,device_id""",
        (group,),
    )
    if not selected:
        return
    data=rows(conn,f"""
        SELECT d.device_id,d.name,uh.local_hour,
               COALESCE(uh.rx_bytes,0)+COALESCE(uh.tx_bytes,0) AS total
        FROM usage_hourly uh JOIN devices d ON d.device_id=uh.device_id
        WHERE uh.local_date=? AND d.enabled=1 AND d.group_name=?
        ORDER BY uh.local_hour,d.device_id
    """,(date,group))
    vals={(int(r["device_id"]),int(r["local_hour"])):int(r["total"]) for r in data}
    print(f"{group.upper()} — HOURLY BANDWIDTH")
    print("────────────────────────────────────────────────────────────────────────────")
    width=max(16, max(len(str(r["name"])) + 2 for r in selected))
    print(f"{'Hour':<6}"+"".join(f"{r['name']:<{width}}" for r in selected))
    print("─"*(6+width*len(selected)))
    totals={int(r["device_id"]):0 for r in selected}
    active_hours=[
        hour for hour in range(24)
        if any(vals.get((int(r["device_id"]),hour),0) > 0 for r in selected)
    ]
    for hour in active_hours:
        out=[]
        for device in selected:
            device_id=int(device["device_id"])
            v=vals.get((device_id,hour),0); totals[device_id]+=v
            out.append(f"{fmt_bytes(v):>{width}}")
        print(f"{hour:02d}:00 "+"".join(out))
    print("─"*(6+width*len(selected)))
    print(f"{'TOTAL':<6}"+"".join(f"{fmt_bytes(totals[int(r['device_id'])]):>{width}}" for r in selected))
    print()


def print_group_summary(known, group_name, label, conn):
    group=[r for r in known if r["group_name"]==group_name]
    count=conn.execute("SELECT COUNT(*) FROM devices WHERE enabled=1 AND group_name=?",(group_name,)).fetchone()[0]
    rx=sum(int(r["rx"]) for r in group)
    tx=sum(int(r["tx"]) for r in group)
    total=sum(int(r["total"]) for r in group)
    print(label); print("────────────────────────────────────────────────")
    print(f"{count:<3} devices   ↓ {fmt_bytes(rx):>10}   ↑ {fmt_bytes(tx):>10}   TOTAL {fmt_bytes(total):>10}")
    print("No alerts\n")


def print_device_usage(known, group_name, label, conn):
    """Show per-device totals for a group while retaining its group summary."""
    group = [r for r in known if r["group_name"] == group_name]
    count = conn.execute(
        "SELECT COUNT(*) FROM devices WHERE enabled=1 AND group_name=?", (group_name,)
    ).fetchone()[0]
    if not count:
        return
    by_id = {int(r["device_id"]): r for r in group}
    all_devices = rows(
        conn,
        """SELECT device_id,name FROM devices
           WHERE enabled=1 AND group_name=? ORDER BY name,device_id""",
        (group_name,),
    )
    print(f"{label} — DEVICE USAGE")
    print("────────────────────────────────────────────────")
    print(f"{'Device':<24} {'↓ Download':>10} {'↑ Upload':>10} {'Total':>10}")
    for device in all_devices:
        usage = by_id.get(int(device["device_id"]))
        rx = int(usage["rx"]) if usage else 0
        tx = int(usage["tx"]) if usage else 0
        total = int(usage["total"]) if usage else 0
        print(f"{device['name']:<24} {fmt_bytes(rx):>10} {fmt_bytes(tx):>10} {fmt_bytes(total):>10}")
    print()


def print_unresolved(data):
    print("NEW / UNRESOLVED\n────────────────────────────────────────────────")
    if not data: print("None")
    else:
        maximum=max(int(r["total"]) for r in data)
        for r in data:
            print(f"{r['ip']:<16} {bar(int(r['total']),maximum):<20} {fmt_bytes(int(r['total'])):>10}")
            print(f"  ↓ {fmt_bytes(int(r['rx'])):>9}   ↑ {fmt_bytes(int(r['tx'])):>9}")
    print()


def print_alerts(data):
    print("ALERTS\n────────────────────────────────────────────────")
    if not data: print("None")
    else:
        for r in data: print(f"⚠ Unresolved client — {r['ip']} — {fmt_bytes(int(r['total']))}")
    print()


def telegram_group_summary(known, group_name, label, conn, details=False):
    group = [r for r in known if r["group_name"] == group_name]
    count = conn.execute(
        "SELECT COUNT(*) FROM devices WHERE enabled=1 AND group_name=?", (group_name,)
    ).fetchone()[0]
    if not count:
        return
    by_id = {int(r["device_id"]): r for r in group}
    rx = sum(int(r["rx"]) for r in group)
    tx = sum(int(r["tx"]) for r in group)
    total = sum(int(r["total"]) for r in group)
    print(f"*{label}*")
    print(f"{count} devices")
    print(f"↓ Download: {markdown_v2(fmt_bytes(rx))}")
    print(f"↑ Upload: {markdown_v2(fmt_bytes(tx))}")
    print(f"Total: {markdown_v2(fmt_bytes(total))}")
    if details:
        print("\n*Devices*")
        devices = rows(
            conn,
            """SELECT device_id,name FROM devices
               WHERE enabled=1 AND group_name=? ORDER BY name,device_id""",
            (group_name,),
        )
        for index, device in enumerate(devices):
            usage = by_id.get(int(device["device_id"]))
            device_rx = int(usage["rx"]) if usage else 0
            device_tx = int(usage["tx"]) if usage else 0
            device_total = int(usage["total"]) if usage else 0
            marker = DEVICE_MARKERS[index % len(DEVICE_MARKERS)]
            print(f"{marker} {markdown_v2(device['name'])}")
            print(f"  ↓ {markdown_v2(fmt_bytes(device_rx))} · ↑ {markdown_v2(fmt_bytes(device_tx))} · Total: {markdown_v2(fmt_bytes(device_total))}")
    print("No alerts\n")


def print_telegram_router_health(conn: sqlite3.Connection, date: str) -> bool:
    data = rows(
        conn,
        """SELECT local_hour,
                  SUM(cpu_sum_x100) AS cpu_sum_x100,
                  MIN(cpu_min_x100) AS cpu_min_x100,
                  MAX(cpu_max_x100) AS cpu_max_x100,
                  SUM(cpu_samples) AS cpu_samples,
                  SUM(memory_used_sum_bytes) AS memory_used_sum_bytes,
                  MIN(memory_used_min_bytes) AS memory_used_min_bytes,
                  MAX(memory_used_max_bytes) AS memory_used_max_bytes,
                  MAX(memory_total_bytes) AS memory_total_bytes,
                  SUM(load_sum_x100) AS load_sum_x100,
                  MIN(load_min_x100) AS load_min_x100,
                  MAX(load_max_x100) AS load_max_x100,
                  SUM(sample_count) AS sample_count
           FROM router_health_hourly
           WHERE local_date=?
           GROUP BY local_hour ORDER BY local_hour""",
        (date,),
    )
    if not data:
        return False

    by_hour = {int(row["local_hour"]): row for row in data}
    cpu_values: list[int | None] = []
    memory_values: list[int | None] = []
    load_values: list[int | None] = []
    for hour in range(24):
        row = by_hour.get(hour)
        if row is None:
            cpu_values.append(None)
            memory_values.append(None)
            load_values.append(None)
            continue
        cpu_samples = int(row["cpu_samples"])
        sample_count = int(row["sample_count"])
        cpu_values.append(
            round(int(row["cpu_sum_x100"]) / cpu_samples) if cpu_samples else None
        )
        memory_values.append(
            round(
                int(row["memory_used_sum_bytes"]) * 10000
                / sample_count
                / int(row["memory_total_bytes"])
            )
            if sample_count else None
        )
        load_values.append(
            round(int(row["load_sum_x100"]) / sample_count) if sample_count else None
        )

    load_max = max((value for value in load_values if value is not None), default=0)
    cpu_sparkline = telegram_bucket_values(cpu_values)
    memory_sparkline = telegram_bucket_values(memory_values)
    load_sparkline = telegram_bucket_values(load_values)
    print("*ROUTER · 24H HEALTH*")
    print("Hour   00 02 04 06 08 10 12 14 16 18 20 22")
    print(f"CPU %  {telegram_sparkline(cpu_sparkline, 10000)}")
    print(f"RAM %  {telegram_sparkline(memory_sparkline, 10000)}")
    print(f"Load   {telegram_sparkline(load_sparkline, load_max)}")

    cpu_peak_hour = max(
        ((value, hour) for hour, value in enumerate(cpu_values) if value is not None),
        default=(0, 0),
    )
    memory_peak_hour = max(
        ((value, hour) for hour, value in enumerate(memory_values) if value is not None),
        default=(0, 0),
    )
    load_peak_hour = max(
        ((value, hour) for hour, value in enumerate(load_values) if value is not None),
        default=(0, 0),
    )
    cpu_samples = sum(int(row["cpu_samples"]) for row in data)
    sample_count = sum(int(row["sample_count"]) for row in data)
    cpu_average = round(
        sum(int(row["cpu_sum_x100"]) for row in data) / cpu_samples
    ) if cpu_samples else 0
    memory_average = round(
        sum(int(row["memory_used_sum_bytes"]) for row in data) / sample_count
    ) if sample_count else 0
    memory_total = max(int(row["memory_total_bytes"]) for row in data)
    memory_average_pct = round(memory_average * 10000 / memory_total) if memory_total else 0
    load_average = round(
        sum(int(row["load_sum_x100"]) for row in data) / sample_count
    ) if sample_count else 0
    memory_peak = max(int(row["memory_used_max_bytes"]) for row in data)
    print(
        f"CPU avg {markdown_v2(f'{cpu_average / 100:.0f}%')} · "
        f"peak {markdown_v2(f'{cpu_peak_hour[0] / 100:.0f}%')} at {cpu_peak_hour[1]:02d}:00"
    )
    print(
        f"RAM avg {markdown_v2(fmt_bytes(memory_average))} "
        f"\\({markdown_v2(f'{memory_average_pct / 100:.0f}%')}\\) · "
        f"peak {markdown_v2(fmt_bytes(memory_peak))} at {memory_peak_hour[1]:02d}:00"
    )
    print(
        f"Load avg {markdown_v2(f'{load_average / 100:.2f}')} · "
        f"peak {markdown_v2(f'{load_peak_hour[0] / 100:.2f}')} at {load_peak_hour[1]:02d}:00"
    )
    print()
    return True


def print_telegram_hourly_group(
    conn: sqlite3.Connection,
    date: str,
    group_name: str,
    label: str,
    known,
    selected_names: tuple[str, ...] | None = None,
) -> None:
    """Show compact, mobile-friendly hourly activity bars for a device group."""
    devices = rows(
        conn,
        """SELECT device_id,name FROM devices
           WHERE enabled=1 AND group_name=? ORDER BY name,device_id""",
        (group_name,),
    )
    if selected_names is not None:
        selected = [
            device for name in selected_names
            for device in devices if device["name"] == name
        ]
    else:
        selected = devices
    if not selected:
        return

    data = rows(
        conn,
        """SELECT d.name,uh.local_hour,
                  SUM(COALESCE(uh.rx_bytes,0)) AS rx,
                  SUM(COALESCE(uh.tx_bytes,0)) AS tx
           FROM usage_hourly uh JOIN devices d ON d.device_id=uh.device_id
           WHERE uh.local_date=? AND d.enabled=1 AND d.group_name=?
           GROUP BY d.device_id,d.name,uh.local_hour
           ORDER BY d.name,uh.local_hour,d.device_id""",
        (date, group_name),
    )
    vals = {(r["name"], int(r["local_hour"])): (int(r["rx"]), int(r["tx"]))
            for r in data}
    known_by_name = {r["name"]: r for r in known}
    print(f"*{label} · HOURLY USAGE*")
    for index, device in enumerate(selected):
        name = device["name"]
        usage = known_by_name.get(name)
        total = int(usage["total"]) if usage else 0
        marker = DEVICE_MARKERS[index % len(DEVICE_MARKERS)]
        print(f"{marker} {markdown_v2(name)} · {markdown_v2(fmt_bytes(total))}")
        active_hours = [hour for hour in range(24)
                        if sum(vals.get((name, hour), (0, 0))) > 0]
        if not active_hours:
            print("  No traffic")
        else:
            for hour in active_hours:
                rx, tx = vals[(name, hour)]
                print(f"  {hour:02d}:00 · ^ {markdown_v2(fmt_bytes(tx))}"
                      f" v {markdown_v2(fmt_bytes(rx))}")
    print()


def report_daily_telegram_health(conn: sqlite3.Connection, date: str) -> None:
    print(f"📈 *ROUTER HEALTH*\n{markdown_v2(date)}\n")
    if not print_telegram_router_health(conn, date):
        print("No recorded router health for this date\n")


def report_daily_telegram(conn: sqlite3.Connection, date: str) -> None:
    known, unresolved, kt, ut, ot = daily_totals(conn, date)
    known_rx = sum(int(r["rx"]) for r in known)
    known_tx = sum(int(r["tx"]) for r in known)
    unresolved_rx = sum(int(r["rx"]) for r in unresolved)
    unresolved_tx = sum(int(r["tx"]) for r in unresolved)
    unresolved_total = unresolved_rx + unresolved_tx
    print(f"📊 *DAILY BANDWIDTH*\n{markdown_v2(date)}\n")
    print("*SUMMARY*")
    print(f"↓ Download: {markdown_v2(fmt_bytes(known_rx + unresolved_rx))}")
    print(f"↑ Upload: {markdown_v2(fmt_bytes(known_tx + unresolved_tx))}")
    print(f"Total: {markdown_v2(fmt_bytes(ot))}")
    print(f"Unresolved: {markdown_v2(fmt_bytes(unresolved_total))} — {len(unresolved)} clients")
    print("Quota alerts: None\n")
    hourly, summary, detail = report_groups(conn)
    for group in hourly:
        print_telegram_hourly_group(conn, date, group, group.upper(), known)
    for group in summary:
        telegram_group_summary(known, group, group.upper(), conn)
    for group in detail:
        print_telegram_hourly_group(conn, date, group, group.upper(), known)
    print("*NEW / UNRESOLVED*")
    if not unresolved:
        print("None\n")
    else:
        for row in unresolved:
            print(f"{markdown_v2(row['ip'])} · ↓ {markdown_v2(fmt_bytes(int(row['rx'])))} · ↑ {markdown_v2(fmt_bytes(int(row['tx'])))} · Total: {markdown_v2(fmt_bytes(int(row['total'])))}")
        print()
    print("*ALERTS*")
    if not unresolved:
        print("None")
    else:
        for row in unresolved:
            print(f"⚠ Unresolved client: {markdown_v2(row['ip'])} — {markdown_v2(fmt_bytes(int(row['total'])))}")
    print()


def report_daily(conn: sqlite3.Connection, date: str) -> None:
    known,unresolved,kt,ut,ot=daily_totals(conn,date)
    print(f"DAILY BANDWIDTH — {date}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    known_rx=sum(int(r["rx"]) for r in known)
    known_tx=sum(int(r["tx"]) for r in known)
    unresolved_rx=sum(int(r["rx"]) for r in unresolved)
    unresolved_tx=sum(int(r["tx"]) for r in unresolved)
    print_status(known_rx,known_tx,unresolved_rx,unresolved_tx,len(unresolved))
    hourly, summary, detail = report_groups(conn)
    for group in hourly: print_hourly_grid(conn,date,group)
    for group in summary: print_group_summary(known,group,group.upper(),conn)
    for group in detail: print_device_usage(known,group,group.upper(),conn)
    print_unresolved(unresolved); print_alerts(unresolved)


def report_unresolved(conn):
    print("UNRESOLVED TRAFFIC\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    data=rows(conn,"""SELECT local_date,ip,COALESCE(SUM(rx_bytes),0) rx,COALESCE(SUM(tx_bytes),0) tx,COALESCE(SUM(rx_bytes+tx_bytes),0) total FROM unresolved_usage GROUP BY local_date,ip ORDER BY local_date DESC,total DESC,ip""")
    if not data: print("None")
    for r in data: print(f"{r['local_date']:<12} {r['ip']:<16} ↓ {fmt_bytes(int(r['rx'])):>10} ↑ {fmt_bytes(int(r['tx'])):>10} TOTAL {fmt_bytes(int(r['total']))}")
    print()


def report_hourly(conn,period,device=None):
    print(f"HOURLY BANDWIDTH — {period.upper()}{' — '+device if device else ''}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    if period in ("today","yesterday"):
        date=report_date(0 if period=="today" else -1); condition="uh.local_date=?"; base=(date,)
    else:
        condition="uh.hour_start_utc>=?"; base=(int(datetime.now().timestamp())-86400,)
    extra=" AND d.name=?" if device else ""; args=base+((device,) if device else ())
    data=rows(conn,f"""SELECT uh.local_date,uh.local_hour,d.name,d.group_name,COALESCE(uh.rx_bytes,0) rx,COALESCE(uh.tx_bytes,0) tx,COALESCE(uh.rx_bytes+uh.tx_bytes,0) total FROM usage_hourly uh JOIN devices d ON d.device_id=uh.device_id WHERE {condition} AND d.enabled=1{extra} ORDER BY uh.local_date,uh.local_hour,d.device_id""",args)
    if not data: print("No recorded traffic for this period."); return
    maximum=max(int(r["total"]) for r in data)
    for r in data: print(f"{int(r['local_hour']):02d}:00  {r['name']:<18} {bar(int(r['total']),maximum,30):<30} {fmt_bytes(int(r['total'])):>10} (↓ {fmt_bytes(int(r['rx']))}, ↑ {fmt_bytes(int(r['tx']))})")


def report_destinations(
    conn: sqlite3.Connection,
    period: str,
    device: str | None = None,
    limit: int = 100,
    minimum_bytes: int = 0,
) -> None:
    """Show sampled per-device destination-IP traffic with DNS clues."""
    if limit < 1 or limit > 1000:
        raise SystemExit("destination report limit must be between 1 and 1000")
    if minimum_bytes < 0:
        raise SystemExit("destination report minimum bytes cannot be negative")

    if period in ("today", "yesterday") or re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        date = report_date(0 if period == "today" else -1) if period in ("today", "yesterday") else period
        condition = "du.local_date=?"
        base_args: list[object] = [date]
        title = date
    else:
        condition = "du.bucket_start_utc>=?"
        base_args = [int(datetime.now().timestamp()) - 86400]
        title = "last 24 hours"

    device_condition = ""
    if device:
        device_condition = " AND d.name=?"
        base_args.append(device)

    sql = f"""
        WITH destination_usage AS (
            SELECT hour_start_utc AS bucket_start_utc,
                   local_date,local_hour,0 AS local_minute,
                   identity_key,device_id,client_ip,destination_ip,rx_bytes,tx_bytes
            FROM destination_usage_hourly
            UNION ALL
            SELECT quarter_start_utc AS bucket_start_utc,
                   local_date,local_hour,local_minute,
                   identity_key,device_id,client_ip,destination_ip,rx_bytes,tx_bytes
            FROM destination_usage_quarterly
        )
        SELECT du.local_date,du.local_hour,COALESCE(d.name,du.identity_key) AS name,
               du.local_minute,
               du.client_ip,du.destination_ip,
               COALESCE(du.rx_bytes,0) AS rx,
               COALESCE(du.tx_bytes,0) AS tx,
               COALESCE(du.rx_bytes,0)+COALESCE(du.tx_bytes,0) AS total,
               (SELECT group_concat(domain, ', ') FROM (
                    SELECT DISTINCT dd.domain
                    FROM destination_domain_observations dd
                    WHERE dd.client_ip=du.client_ip
                      AND dd.destination_ip=du.destination_ip
                    ORDER BY dd.domain
               )) AS domains
        FROM destination_usage du
        LEFT JOIN devices d ON d.device_id=du.device_id
        WHERE {condition}
          AND (d.device_id IS NULL OR d.enabled=1)
          {device_condition}
          AND COALESCE(du.rx_bytes,0)+COALESCE(du.tx_bytes,0)>=?
        ORDER BY total DESC,du.local_date DESC,du.local_hour DESC,
                 du.local_minute DESC,name,du.destination_ip
        LIMIT ?
    """
    data = rows(conn, sql, tuple(base_args + [minimum_bytes, limit]))
    print(
        f"DESTINATION BANDWIDTH — {title}"
        f"{' — ' + device if device else ''}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    if not data:
        print("No destination traffic recorded for this period.")
        return
    print("DNS names are observed AdGuard clues; destination IP counters are authoritative.")
    for row in data:
        domains = row["domains"] or "-"
        print(
            f"{row['local_date']} {int(row['local_hour']):02d}:{int(row['local_minute']):02d}  "
            f"{row['name']:<20} {row['client_ip']:<15} -> "
            f"{row['destination_ip']:<15}  {domains}\n"
            f"  ↓ {fmt_bytes(int(row['rx'])):>10}  "
            f"↑ {fmt_bytes(int(row['tx'])):>10}  "
            f"TOTAL {fmt_bytes(int(row['total'])):>10}"
        )


def main():
    parser=argparse.ArgumentParser(description="Traffic accounting reports")
    parser.add_argument("--db",default=DB_DEFAULT)
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("daily",help="Daily report; offset 0 to -14")
    p.add_argument("offset",nargs="?",type=int,default=0)
    p.add_argument("--telegram", action="store_true", help="mobile-friendly Telegram layout")
    p.add_argument("--telegram-health", action="store_true", help="Telegram-only router health layout")
    sub.add_parser("unresolved")
    p=sub.add_parser("hourly"); p.add_argument("period",choices=("today","yesterday","24h")); p.add_argument("device",nargs="?")
    p=sub.add_parser("destinations",help="sampled destination-IP traffic")
    p.add_argument("period",help="today, yesterday, 24h, or YYYY-MM-DD")
    p.add_argument("device",nargs="?")
    p.add_argument("--limit",type=int,default=100)
    p.add_argument("--min-bytes",type=int,default=0)
    args=parser.parse_args()
    if args.command=="daily":
        if not -14<=args.offset<=0: parser.error("daily offset must be between -14 and 0")
        if args.telegram and args.telegram_health: parser.error("choose one Telegram report layout")
    with sqlite3.connect(args.db) as conn:
        conn.row_factory=sqlite3.Row
        if args.command=="daily":
            date = report_date(args.offset)
            if args.telegram_health:
                report_daily_telegram_health(conn, date)
            elif args.telegram:
                report_daily_telegram(conn, date)
            else:
                report_daily(conn, date)
        elif args.command=="unresolved": report_unresolved(conn)
        elif args.command=="hourly": report_hourly(conn,args.period,args.device)
        elif args.command=="destinations":
            report_destinations(conn,args.period,args.device,args.limit,args.min_bytes)

if __name__=="__main__": main()
