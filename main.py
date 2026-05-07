import asyncio
import json
import logging
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateutil import parser as dateutil_parser
from dotenv import load_dotenv
from googleapiclient.errors import HttpError

from gcal import CalendarClient


def make_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    return commands.Bot(command_prefix=commands.when_mentioned, intents=intents, help_command=None)


async def sync_commands(bot: commands.Bot, guild_id: int | None) -> None:
    synced = await bot.tree.sync()
    print(f"Synced {len(synced)} commands globally.")
    if guild_id:
        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(guild=guild)
        guild_synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(guild_synced)} commands to guild {guild_id}")

load_dotenv()

logging.basicConfig(level=logging.INFO)

# ── Setup ────────────────────────────────────────────────────────────────────

TZ           = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tokyo"))
GUILD_ID     = int(os.getenv("DISCORD_GUILD_ID")) if os.getenv("DISCORD_GUILD_ID") else None
CHANNEL_ID   = int(os.getenv("DISCORD_CHANNEL_ID")) if os.getenv("DISCORD_CHANNEL_ID") else None
ADMIN_IDS    = {int(i) for i in os.getenv("DISCORD_ADMIN_IDS", "").split(",") if i.strip()}

_SUBJECTS_PATH = os.path.join(os.path.dirname(__file__), "subjects.json")
_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")
_ID_MAP_PATH   = os.path.join(os.path.dirname(__file__), "id_map.json")

bot       = make_bot()
cal       = CalendarClient()
scheduler = AsyncIOScheduler(timezone=TZ)

# Persistent map: display ID (int) -> Google Calendar event ID (str)
def _load_id_map() -> dict[int, str]:
    if os.path.exists(_ID_MAP_PATH):
        with open(_ID_MAP_PATH, encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    return {}

def _save_id_map(m: dict[int, str]) -> None:
    with open(_ID_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in m.items()}, f, ensure_ascii=False, indent=2)

_id_map: dict[int, str] = _load_id_map()

def _assign_id(event_id: str) -> int:
    next_id = max(_id_map.keys(), default=0) + 1
    _id_map[next_id] = event_id
    _save_id_map(_id_map)
    return next_id

def _release_id(event_id: str) -> None:
    key = next((k for k, v in _id_map.items() if v == event_id), None)
    if key is not None:
        del _id_map[key]
        _save_id_map(_id_map)


# ── Settings ──────────────────────────────────────────────────────────────────

def _load_settings() -> dict:
    with open(_SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)

def _save_settings(data: dict) -> None:
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Subjects ──────────────────────────────────────────────────────────────────

_subjects_cache: list[str] = []

def _load_subjects() -> list[str]:
    with open(_SUBJECTS_PATH, encoding="utf-8") as f:
        return json.load(f)

def _get_subjects() -> list[str]:
    global _subjects_cache
    if _subjects_cache:
        return _subjects_cache
    try:
        _subjects_cache = _load_subjects()
    except Exception:
        logging.exception("subjects.json の読み込みに失敗しました: %s", _SUBJECTS_PATH)
        _subjects_cache = []
    return _subjects_cache


# ── Subject Autocomplete ──────────────────────────────────────────────────────

async def autocomplete_subject(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    try:
        subjects = _get_subjects()
        return [
            app_commands.Choice(name=s, value=s)
            for s in subjects
            if current.lower() in s.lower()
        ][:25]
    except Exception:
        logging.exception("autocomplete_subject でエラーが発生しました")
        return []


# ── Interaction Helpers ───────────────────────────────────────────────────────

async def _defer(interaction: discord.Interaction, ephemeral: bool = False) -> bool:
    """defer() して True を返す。3秒タイムアウト済みなら False を返す。"""
    try:
        await interaction.response.defer(ephemeral=ephemeral)
        return True
    except discord.NotFound:
        return False


# ── Date/Time Helpers ─────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(tz=TZ)

def _today() -> date:
    return _now().date()

def _sod(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=TZ)

def _eod(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=TZ)

def _end_of_week() -> date:
    d = _today()
    return d + timedelta(days=(6 - d.weekday()))

def _parse_date(text: str) -> datetime:
    t = text.strip()
    relative: dict[str, date] = {
        "today":    _today(),
        "今日":      _today(),
        "tomorrow": _today() + timedelta(days=1),
        "明日":      _today() + timedelta(days=1),
        "明後日":    _today() + timedelta(days=2),
    }
    if t.lower() in relative or t in relative:
        d = relative.get(t.lower()) or relative[t]
        return datetime(d.year, d.month, d.day, tzinfo=TZ)

    m = re.match(
        r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日?"
        r"(?:\s*(\d{1,2})時(\d{0,2})分?)?",
        t,
    )
    if m:
        year_s, mo_s, day_s, hr_s, min_s = m.groups()
        year = int(year_s) if year_s else _today().year
        return datetime(year, int(mo_s), int(day_s),
                        int(hr_s) if hr_s else 0,
                        int(min_s) if min_s else 0,
                        tzinfo=TZ)

    m2 = re.fullmatch(r"(\d{1,2})[/\-](\d{1,2})", t)
    if m2:
        mo, day = int(m2.group(1)), int(m2.group(2))
        candidate = datetime(_today().year, mo, day, tzinfo=TZ)
        if candidate.date() < _today():
            candidate = candidate.replace(year=candidate.year + 1)
        return candidate

    dt = dateutil_parser.parse(t, dayfirst=False)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


# ── Notification Helpers ──────────────────────────────────────────────────────

def _parse_lead(spec: str | None) -> timedelta | None:
    """
    Returns timedelta (lead before due), timedelta(0) for 当日 (08:00 on due date), None for off.
    Raises ValueError on invalid input.
    """
    if not spec or spec in ("off", "0"):
        return None
    if spec == "当日":
        return timedelta(0)
    if spec.endswith("d"):
        return timedelta(days=int(spec[:-1]))
    raise ValueError(f"invalid timing: {spec!r}  (例: 1d / 当日 / off)")


def _notify_at(event: dict, lead: timedelta) -> datetime | None:
    """Returns the datetime to fire the notification, or None if it's already past."""
    due = _event_dt(event)
    if lead == timedelta(0):
        # 当日: 08:00 on the due date
        d = due.date()
        fire = datetime(d.year, d.month, d.day, 8, 0, tzinfo=TZ)
    else:
        # Lead days: 20:00 on d-lead date
        d = (due - lead).date()
        fire = datetime(d.year, d.month, d.day, 20, 0, tzinfo=TZ)

    return fire if fire > _now() else None


async def _send_notify(event_id: str) -> None:
    if not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    ch = guild.get_channel(CHANNEL_ID)
    if not ch:
        return
    try:
        ev   = cal.get_task(event_id)
        name = _task_name(ev)
        subj = _subject(ev)
        due  = _fmt_due(ev)
        await ch.send(f"```\n[reminder] [{subj}] {name}  due {due}\n```")
    except Exception:
        pass


def _schedule_notify(event: dict) -> None:
    """Schedule a notification job for the given event based on current settings."""
    lead = _parse_lead(_load_settings().get("notify_lead"))
    if lead is None:
        return
    fire = _notify_at(event, lead)
    if fire is None:
        return
    job_id = f"notify_{event['id']}"
    scheduler.add_job(
        _send_notify,
        trigger="date",
        run_date=fire,
        args=[event["id"]],
        id=job_id,
        replace_existing=True,
    )


def _cancel_notify(event_id: str) -> None:
    job_id = f"notify_{event_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def _reschedule_all() -> None:
    """Fetch all future tasks and reschedule notifications from scratch."""
    lead = _parse_lead(_load_settings().get("notify_lead"))
    # Cancel all existing notify jobs
    for job in scheduler.get_jobs():
        if job.id.startswith("notify_"):
            scheduler.remove_job(job.id)
    if lead is None:
        return
    try:
        events = cal.list_tasks(time_min=_now(), max_results=200)
        for ev in events:
            _schedule_notify(ev)
    except Exception:
        pass


async def _auto_cleanup() -> None:
    """Delete tasks that ended before today and notify the channel."""
    if not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    ch = guild.get_channel(CHANNEL_ID)
    if not ch:
        return

    # SOD of today is the cutoff
    time_max = _sod(_today())
    # We fetch a history range (e.g., past 30 days) to find missed cleanups, or just anything before today
    time_min = time_max - timedelta(days=30)

    try:
        # Use list_tasks but we need everything before today. 
        # list_tasks in gcal.py uses timeMin. We might need a slightly different query or just handle it.
        # Actually gcal.py's list_tasks has time_max.
        events = cal.list_tasks(time_min=time_min, time_max=time_max)
        if not events:
            return

        deleted_info = []
        for ev in events:
            ev_id = ev["id"]
            name = _task_name(ev)
            subj = _subject(ev)
            
            try:
                cal.delete_task(ev_id)
                _cancel_notify(ev_id)
                _release_id(ev_id)
                deleted_info.append(f"[{subj}] {name}")
            except Exception:
                pass

        if deleted_info:
            msg = "\n".join(deleted_info)
            await ch.send(f"```\n[auto-cleanup] Deleted past tasks:\n{msg}\n```", silent=True)

    except Exception as e:
        print(f"Cleanup error: {e}")


# ── Event Helpers ─────────────────────────────────────────────────────────────

def _is_allday(event: dict) -> bool:
    return "date" in event["start"]

def _event_dt(event: dict) -> datetime:
    start = event["start"]
    if "date" in start:
        return datetime.strptime(start["date"], "%Y-%m-%d").replace(tzinfo=TZ)
    return datetime.fromisoformat(start["dateTime"]).astimezone(TZ)

def _subject(event: dict) -> str:
    return event.get("extendedProperties", {}).get("private", {}).get("subject", "?")

def _fmt_due(event: dict) -> str:
    dt = _event_dt(event)
    return dt.strftime("%Y/%m/%d") if _is_allday(event) else dt.strftime("%Y/%m/%d-%H:%M")

def _fmt_due_short(event: dict) -> str:
    dt = _event_dt(event)
    return dt.strftime("%m/%d") if _is_allday(event) else dt.strftime("%m/%d-%H:%M")

def _task_name(event: dict) -> str:
    summary = event.get("summary", "?")
    subj = _subject(event)
    prefix = f"{subj}-"
    if summary.startswith(prefix):
        return summary[len(prefix):]
    return summary


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt_table(events: list) -> discord.Embed:
    embed = discord.Embed(color=0x5865F2)

    if not events:
        embed.description = "*(no assignments found)*"
        return embed

    rev_map = {v: k for k, v in _id_map.items()}
    shown = events[:25]
    lines = ["**ID | Subject | Name | Due**"]
    for ev in shown:
        ev_id = ev["id"]
        if ev_id not in rev_map:
            new_id = _assign_id(ev_id)
            rev_map[ev_id] = new_id
        display_id = rev_map[ev_id]
        subj = _subject(ev)
        name = _task_name(ev)
        due  = _fmt_due_short(ev)
        lines.append(f"`{display_id:>2}` | {subj} | {name} | `{due}`")

    embed.description = "\n".join(lines)
    total = len(events)
    note  = f"先頭 25 / {total} 件を表示" if total > 25 else f"{total} 件"
    embed.set_footer(text=note)
    return embed


def _sort_events(events: list, sort_val: str) -> list:
    field, direction = sort_val.rsplit("_", 1)
    desc = direction == "desc"
    rev_map = {v: k for k, v in _id_map.items()}
    key_map = {
        "id":      lambda e: rev_map.get(e["id"], 0),
        "subject": lambda e: _subject(e),
        "name":    lambda e: _task_name(e),
        "due":     lambda e: _event_dt(e),
    }
    return sorted(events, key=key_map[field], reverse=desc)


def _fmt_detail(ev: dict, display_id: int | None = None) -> str:
    lines = []
    if display_id is not None:
        lines.append(f"ID      : {display_id}")
    lines += [
        f"Name    : {_task_name(ev)}",
        f"Subject : {_subject(ev)}",
        f"Due     : {_fmt_due(ev)}",
        f"Notes   : {ev.get('description') or '(none)'}",
    ]
    return "```\n" + "\n".join(lines) + "\n```"


# ── Bot Events ────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    scheduler.start()
    _reschedule_all()

    # Schedule daily cleanup at 00:00
    scheduler.add_job(_auto_cleanup, trigger="cron", hour=0, minute=0, id="auto_cleanup", replace_existing=True)

    _get_subjects()  # subjects.json をキャッシュ（エラーはここで表面化させる）

    await sync_commands(bot, GUILD_ID)

    print(f"ready: {bot.user}  |  guild={GUILD_ID}  channel_id={CHANNEL_ID}")


# ── /mk ───────────────────────────────────────────────────────────────────────

@bot.tree.command(name="mk", description="課題を新規登録する")
@app_commands.describe(
    subject="科目名",
    name="課題名",
    due="期限  例: 12/31 / 12月31日 / 明日 / 2025-01-15",
    time="締切時刻  例: 13:00",
)
@app_commands.autocomplete(subject=autocomplete_subject)
async def cmd_mk(interaction: discord.Interaction, subject: str, name: str, due: str, time: str | None = None):
    if not await _defer(interaction): return
    try:
        due_dt = _parse_date(due)
    except Exception:
        await interaction.followup.send(f"```\nmk: invalid date: '{due}'\n```", ephemeral=True)
        return

    if time is not None:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", time.strip())
        if not m:
            await interaction.followup.send(f"```\nmk: invalid time: '{time}'  (例: 13:00)\n```", ephemeral=True)
            return
        due_dt = due_dt.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0)

    try:
        event = cal.add_task(subject, name, due_dt)
        _assign_id(event["id"])
        _schedule_notify(event)
        due_str = _fmt_due(event)
        await interaction.followup.send(f"```\ncreated: [{subject}] {name}  due {due_str}\n```")
    except HttpError as e:
        await interaction.followup.send(f"```\nmk: calendar error: {e.reason}\n```", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"```\nmk: error: {e}\n```", ephemeral=True)


# ── /ls ───────────────────────────────────────────────────────────────────────

@bot.tree.command(name="ls", description="課題の一覧を表示する")
@app_commands.describe(
    filter="絞り込み期間  (省略時: all)",
    subject="科目名でフィルタリング",
    sort="ソート順  (省略時: Due ↑)",
)
@app_commands.choices(filter=[
    app_commands.Choice(name="-t  today    本日が期限",    value="t"),
    app_commands.Choice(name="-tm tomorrow 明日が期限",    value="tm"),
    app_commands.Choice(name="-w  week     今週が期限",    value="w"),
    app_commands.Choice(name="-a  all      すべての未完了", value="a"),
])
@app_commands.choices(sort=[
    app_commands.Choice(name="ID      ↑ asc",      value="id_asc"),
    app_commands.Choice(name="ID      ↓ desc",     value="id_desc"),
    app_commands.Choice(name="Subject ↑ asc",      value="subject_asc"),
    app_commands.Choice(name="Subject ↓ desc",     value="subject_desc"),
    app_commands.Choice(name="Name    ↑ asc",      value="name_asc"),
    app_commands.Choice(name="Name    ↓ desc",     value="name_desc"),
    app_commands.Choice(name="Due     ↑ asc",      value="due_asc"),
    app_commands.Choice(name="Due     ↓ desc",     value="due_desc"),
])
@app_commands.autocomplete(subject=autocomplete_subject)
async def cmd_ls(
    interaction: discord.Interaction,
    filter: app_commands.Choice[str] | None = None,
    subject: str | None = None,
    sort: app_commands.Choice[str] | None = None,
):
    if not await _defer(interaction, ephemeral=True): return

    td = _today()
    time_min = _now()
    time_max = None

    fval = filter.value if filter else "a"
    if fval == "t":
        time_min, time_max = _sod(td), _eod(td)
    elif fval == "tm":
        tmr = td + timedelta(days=1)
        time_min, time_max = _sod(tmr), _eod(tmr)
    elif fval == "w":
        time_min, time_max = _sod(td), _eod(_end_of_week())

    try:
        events = cal.list_tasks(time_min=time_min, time_max=time_max, subject=subject)
        if sort:
            events = _sort_events(events, sort.value)
        await interaction.followup.send(embed=_fmt_table(events))
    except HttpError as e:
        await interaction.followup.send(f"```\nls: calendar error: {e.reason}\n```", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"```\nls: error: {e}\n```", ephemeral=True)


# ── /cat ──────────────────────────────────────────────────────────────────────

@bot.tree.command(name="cat", description="課題の詳細を表示する")
@app_commands.describe(
    id="IDで検索",
    name="課題名で検索 (部分一致)",
    subject="科目名で検索し一覧表示",
)
@app_commands.autocomplete(subject=autocomplete_subject)
async def cmd_cat(
    interaction: discord.Interaction,
    id: int | None = None,
    name: str | None = None,
    subject: str | None = None,
):
    if id is None and name is None and subject is None:
        await interaction.response.send_message(
            "```\ncat: missing operand: id / name / subject のいずれかを指定してください\n```",
            ephemeral=True,
        )
        return

    if not await _defer(interaction, ephemeral=True): return

    try:
        if id is not None:
            event_id = _id_map.get(id)
            if not event_id:
                await interaction.followup.send(
                    f"```\ncat: {id}: not found\n```", ephemeral=True
                )
                return
            ev = cal.get_task(event_id)
            await interaction.followup.send(_fmt_detail(ev, display_id=id))
        elif name is not None:
            events = cal.search_by_name(name, _now())
            if not events:
                await interaction.followup.send(
                    f"```\ncat: '{name}': no matching assignment\n```", ephemeral=True
                )
                return
            rev_map = {v: k for k, v in _id_map.items()}
            for ev in events[:5]:
                await interaction.followup.send(_fmt_detail(ev, display_id=rev_map.get(ev["id"])), ephemeral=True)
        else:
            events = cal.list_tasks(time_min=_now(), subject=subject)
            if not events:
                await interaction.followup.send(
                    f"```\ncat: '{subject}': no assignments found\n```", ephemeral=True
                )
                return
            await interaction.followup.send(embed=_fmt_table(events))
    except HttpError as e:
        await interaction.followup.send(f"```\ncat: calendar error: {e.reason}\n```", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"```\ncat: error: {e}\n```", ephemeral=True)


# ── /rm ───────────────────────────────────────────────────────────────────────

@bot.tree.command(name="rm", description="課題をIDで削除する")
@app_commands.describe(id="/ls または /top で表示されたID")
async def cmd_rm(interaction: discord.Interaction, id: int):
    if not await _defer(interaction): return

    event_id = _id_map.get(id)
    if not event_id:
        await interaction.followup.send(
            f"```\nrm: {id}: not found\n```", ephemeral=True
        )
        return

    try:
        ev   = cal.get_task(event_id)
        name = _task_name(ev)
        subj = _subject(ev)
        cal.delete_task(event_id)
        _cancel_notify(event_id)
        _release_id(event_id)
        await interaction.followup.send(f"```\nremoved: [{subj}] {name}\n```")
    except HttpError as e:
        if e.resp.status in (404, 410):
            await interaction.followup.send(
                f"```\nrm: {id}: event already deleted\n```", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"```\nrm: calendar error: {e.reason}\n```", ephemeral=True
            )
    except Exception as e:
        await interaction.followup.send(f"```\nrm: error: {e}\n```", ephemeral=True)


# ── /edit ─────────────────────────────────────────────────────────────────────

@bot.tree.command(name="edit", description="課題の名前・科目・メモを変更する")
@app_commands.describe(
    id="/ls または /top で表示されたID",
    name="新しい課題名 (省略で変更なし)",
    subject="新しい科目名 (省略で変更なし)",
    notes="メモ・説明 (省略で変更なし)",
)
@app_commands.autocomplete(subject=autocomplete_subject)
async def cmd_edit(
    interaction: discord.Interaction,
    id: int,
    name: str | None = None,
    subject: str | None = None,
    notes: str | None = None,
):
    if name is None and subject is None and notes is None:
        await interaction.response.send_message(
            "```\nedit: missing operand: name / subject / notes のいずれかを指定してください\n```",
            ephemeral=True,
        )
        return

    if not await _defer(interaction, ephemeral=True): return

    event_id = _id_map.get(id)
    if not event_id:
        await interaction.followup.send(
            f"```\nedit: {id}: not found\n```", ephemeral=True
        )
        return

    try:
        updated = cal.update_task(event_id, name=name, subject=subject, notes=notes)
        new_name = _task_name(updated)
        new_subj = _subject(updated)
        due_str  = _fmt_due(updated)
        # Reschedule notification with updated event
        _cancel_notify(event_id)
        _schedule_notify(updated)
        await interaction.followup.send(
            f"```\nupdated: [{new_subj}] {new_name}  due {due_str}\n```"
        )
    except HttpError as e:
        await interaction.followup.send(f"```\nedit: calendar error: {e.reason}\n```", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"```\nedit: error: {e}\n```", ephemeral=True)


# ── /top ──────────────────────────────────────────────────────────────────────

@bot.tree.command(name="top", description="期限が近い課題を指定件数表示する")
@app_commands.describe(count="表示件数 (省略時: 5)")
async def cmd_top(interaction: discord.Interaction, count: int = 5):
    if not await _defer(interaction, ephemeral=True): return
    try:
        events = cal.list_tasks(time_min=_now(), max_results=count)
        await interaction.followup.send(embed=_fmt_table(events))
    except HttpError as e:
        await interaction.followup.send(f"```\ntop: calendar error: {e.reason}\n```", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"```\ntop: error: {e}\n```", ephemeral=True)


# ── /settings ─────────────────────────────────────────────────────────────────

class SettingsGroup(app_commands.Group, name="settings", description="Bot設定を変更・確認する"):

    @app_commands.command(name="notify", description="課題期限の通知タイミングを設定する")
    @app_commands.describe(timing="通知タイミング (省略で現在の設定を表示)")
    @app_commands.choices(timing=[
        app_commands.Choice(name="1d   1日前 20:00", value="1d"),
        app_commands.Choice(name="2d   2日前 20:00", value="2d"),
        app_commands.Choice(name="当日  期限当日 08:00", value="当日"),
        app_commands.Choice(name="off  通知を無効化",    value="off"),
    ])
    async def notify(
        self,
        interaction: discord.Interaction,
        timing: app_commands.Choice[str] | None = None,
    ):
        s = _load_settings()

        if timing is None:
            current = s.get("notify_lead") or "off"
            await interaction.response.send_message(
                f"```\nnotify_lead : {current}\n```", ephemeral=True
            )
            return

        s["notify_lead"] = timing.value if timing.value != "off" else None
        _save_settings(s)
        _reschedule_all()

        display = timing.value if timing.value != "off" else "off"
        await interaction.response.send_message(
            f"```\nnotify_lead : {display}  (全課題の通知を再スケジュールしました)\n```",
            ephemeral=True,
        )


bot.tree.add_command(SettingsGroup())


# ── /ping ─────────────────────────────────────────────────────────────────────

@bot.tree.command(name="ping", description="BotのレイテンシをPONG!で返す")
async def cmd_ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"```\nPONG!\nLatency: {latency_ms}ms\n```", ephemeral=True)


# ── /reboot ───────────────────────────────────────────────────────────────────

@bot.tree.command(name="reboot", description="Botを再起動する (管理者専用)")
async def cmd_reboot(interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message(
            f"```\nreboot: {interaction.user.name}: permission denied\n```",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"```\nreboot: initiated by {interaction.user.name}  restarting...\n```",
        ephemeral=True,
    )
    # Docker の restart: always によりコンテナが自動的に再起動される
    sys.exit(0)


# ── /man ──────────────────────────────────────────────────────────────────────

@bot.tree.command(name="man", description="マニュアルを表示する")
async def cmd_man(interaction: discord.Interaction):
    text = (
        "SCHOOL TASK MANAGER(1)                                    User Commands\n"
        "\n"
        "COMMANDS\n"
        "    /mk <subject> <name> <due> [time]\n"
        "        課題登録。due: 12/31, 12月31日, 明日, 今日, 2025-01-15等。\n"
        "    /ls [filter] [subject] [sort]\n"
        "        一覧表示。filter: -t(本日), -tm(明日), -w(今週), -a(全て)。\n"
        "    /cat [id | name | subject]\n"
        "        詳細表示。ID、名称(部分一致)、科目名のいずれかで指定。\n"
        "    /edit <id> [name] [subject] [notes]\n"
        "        編集。ID必須。name, subject, notes のいずれか1つ以上を指定。\n"
        "    /rm <id>\n"
        "        削除。ID必須。\n"
        "    /top [count]\n"
        "        期限の近い順に表示。count省略時は5件。\n"
        "    /settings notify [timing]\n"
        "        通知設定。timing: 1d, 2d, 当日, off。省略で現在の設定を表示。\n"
        "        (1d等は前日20:00、当日は08:00に通知)\n"
        "    /ping\n"
        "        レイテンシ確認。\n"
        "    /reboot\n"
        "        管理者用再起動。\n"
        "\n"
        "DATE FORMATS\n"
        "    今日/today, 明日/tomorrow, 明後日, M/D, M月D日, YYYY-MM-DD\n"
        "    時刻指定: M月D日 H時MM分, 13:00 等\n"
        "\n"
        "NOTES\n"
        "    ・IDは不変。/rm・/cat・/edit で直接指定可能。\n"
        "    ・データはGoogleカレンダーと同期される。"
    )
    await interaction.response.send_message(f"```\n{text}\n```", ephemeral=True)


# ── /gitlatest ────────────────────────────────────────────────────────────────

@bot.tree.command(name="gitlatest", description="GitHubの最新コミット内容を確認する")
async def cmd_gitlatest(interaction: discord.Interaction):
    repo = os.getenv("GITHUB_REPO")
    if not repo:
        await interaction.response.send_message(
            "```\ngitlatest: GITHUB_REPO が .env に設定されていません\n```", ephemeral=True
        )
        return
    if not await _defer(interaction, ephemeral=True): return

    url = f"https://api.github.com/repos/{repo}/commits?per_page=1"

    def _fetch():
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "SchoolTaskManager-Bot"},
        )
        token = os.getenv("GITHUB_TOKEN")
        if token:
            req.add_header("Authorization", f"token {token}")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    try:
        data = await asyncio.to_thread(_fetch)
    except Exception as e:
        await interaction.followup.send(f"```\ngitlatest: error: {e}\n```", ephemeral=True)
        return

    if not data:
        await interaction.followup.send("```\ngitlatest: コミットが見つかりません\n```", ephemeral=True)
        return

    commit = data[0]["commit"]
    sha    = data[0]["sha"][:7]
    msg    = commit["message"]
    author = commit["author"]["name"]
    date   = commit["author"]["date"][:10]

    await interaction.followup.send(
        f"```\n[{repo}]  {sha}  {date}\n{author}\n\n{msg}\n```", ephemeral=True
    )


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set in .env")
    bot.run(token)
