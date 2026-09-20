from __future__ import annotations

# MASTER_MEDIA_TRANSPORT_V11

import asyncio
import json
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import WORK_DIR
from .editorial import telegram_caption
from .media import (
    download_youtube_range,
    extract_range,
    parse_link_and_range,
    parse_timestamp_range,
    probe,
)
from .transport_adapter import bind_broker


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
MAX_INPUT_BYTES = int(os.getenv("MASTER_MAX_INPUT_BYTES", str(8 * 1024**3)))
MAX_TELEGRAM_OUTPUT_BYTES = int(os.getenv("MASTER_TELEGRAM_OUTPUT_BYTES", str(1900 * 1024**2)))
RESULT_TTL_SECONDS = 6 * 3600


@dataclass
class ActiveTask:
    user_id: int
    cancelled: bool = False
    process: Optional[asyncio.subprocess.Process] = None


_ACTIVE: Dict[int, ActiveTask] = {}
_BOT_MODULE = None


def _allowed(update: Update) -> bool:
    if _BOT_MODULE is not None and hasattr(_BOT_MODULE, "is_owner"):
        try:
            if bool(_BOT_MODULE.is_owner(update)):
                return True
        except Exception:
            pass
    owner = int(os.getenv("OWNER_ID", "0") or 0)
    user=update.effective_user
    if user and owner and user.id==owner:
        return True
    if (os.getenv("MASTER_KEY_ACCESS_REQUIRED","1").strip().lower()
            in {"0","false","no","off"}):
        return True
    if user and _BOT_MODULE is not None and hasattr(_BOT_MODULE,"user_has_key_access"):
        try:
            return any(bool(_BOT_MODULE.user_has_key_access(user.id,scope))
                       for scope in ("clipper","editor"))
        except Exception:
            return False
    return False


def _owner(update: Update) -> bool:
    if _BOT_MODULE is not None and hasattr(_BOT_MODULE,"is_owner"):
        try:
            return bool(_BOT_MODULE.is_owner(update))
        except Exception:
            return False
    owner=int(os.getenv("OWNER_ID","0") or 0)
    return bool(update.effective_user and owner and update.effective_user.id==owner)


def _access_text() -> str:
    username=str(getattr(_BOT_MODULE,"OWNER_USERNAME","") or "").strip().lstrip("@")
    admin=f"@{username}" if username else str(getattr(_BOT_MODULE,"OWNER_DISPLAY_NAME","admin") or "admin")
    return (
        "🤖 Public Bot\n\n"
        "This bot uses key-based access. To use the editing tools, get an access key from the admin.\n\n"
        f"Admin: {admin}\n"
        "Activate your key with:\n/key YOUR_KEY"
    )


def _master_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send Full File", callback_data="master:source_full"),
         InlineKeyboardButton("✂️ File + Timestamp", callback_data="master:source_file_range")],
        [InlineKeyboardButton("🔗 YouTube + Timestamp", callback_data="master:source_youtube_range")],
        [InlineKeyboardButton("⬅️ Back", callback_data="master:back")],
    ])


def _result_menu(token: str, *, captionless_sent: bool = False,
                 transcript_sent: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Caption-free Sent" if captionless_sent else "🚫 Remove Captions",
                              callback_data="master:noop" if captionless_sent else f"master:remove_captions:{token}"),
         InlineKeyboardButton("✅ Transcript Sent" if transcript_sent else "📝 Transcript",
                              callback_data="master:noop" if transcript_sent else f"master:transcript:{token}")],
        [InlineKeyboardButton("🎞 New Master Edit",callback_data="master:open")],
    ])


def _master_back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Master Editor", callback_data="master:open")],
        [InlineKeyboardButton("⛔ Stop Master Job", callback_data="master:stop")],
    ])


def _format_time(value: float) -> str:
    seconds = max(0, int(round(float(value))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _pending_paths(context: ContextTypes.DEFAULT_TYPE) -> tuple[Optional[Path], Optional[Path]]:
    source_raw = context.user_data.get("master_pending_source")
    job_raw = context.user_data.get("master_pending_job_dir")
    return (
        Path(str(source_raw)) if source_raw else None,
        Path(str(job_raw)) if job_raw else None,
    )


def _clear_pending(context: ContextTypes.DEFAULT_TYPE, *, remove_files: bool) -> None:
    _, job_dir = _pending_paths(context)
    for key in (
        "master_pending_source",
        "master_pending_job_dir",
        "master_pending_created",
        "master_pending_name",
        "master_choice_token",
        "master_prepared_info",
    ):
        context.user_data.pop(key, None)
    if remove_files and job_dir:
        try:
            resolved = job_dir.resolve()
            work_root = WORK_DIR.resolve()
            if resolved != work_root and work_root in resolved.parents:
                shutil.rmtree(resolved, ignore_errors=True)
        except Exception:
            pass


def _result_paths(context: ContextTypes.DEFAULT_TYPE) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
    clean=context.user_data.get("master_captionless_output")
    transcript=context.user_data.get("master_transcript_output")
    job=context.user_data.get("master_result_job_dir")
    return (Path(str(clean)) if clean else None,
            Path(str(transcript)) if transcript else None,
            Path(str(job)) if job else None)


def _clear_result(context: ContextTypes.DEFAULT_TYPE, *, remove_files: bool) -> None:
    _,_,job_dir=_result_paths(context)
    for key in ("master_result_token","master_captionless_output",
                "master_transcript_output","master_result_job_dir",
                "master_result_created","master_captionless_sent",
                "master_transcript_sent"):
        context.user_data.pop(key,None)
    if remove_files and job_dir:
        try:
            resolved=job_dir.resolve()
            root=WORK_DIR.resolve()
            if resolved!=root and root in resolved.parents:
                shutil.rmtree(resolved,ignore_errors=True)
        except Exception:
            pass


async def _expire_result(context: ContextTypes.DEFAULT_TYPE, token: str) -> None:
    await asyncio.sleep(RESULT_TTL_SECONDS)
    if context.user_data.get("master_result_token")==token:
        _clear_result(context,remove_files=True)


async def _safe_edit(message: Any, text: str, markup=None) -> None:
    try:
        await message.edit_text(text[:4090], reply_markup=markup)
    except TelegramError:
        try:
            await message.reply_text(text[:4090], reply_markup=markup)
        except TelegramError:
            pass


async def _show_menu(message: Any) -> None:
    await _safe_edit(
        message,
        "🎞 Master Editor\n\n"
        "Choose how you want to send the source. Editing starts automatically with "
        "video-matched captions—there is no caption question before rendering.\n\n"
        "Frame director: stable single-speaker close-up; controlled duo/split/diagonal "
        "and crowd grids. Blur-backed layouts are disabled; short multi-person moments "
        "stay sharp using context/split/full-source fallback.\n\n"
        "After delivery, use Remove Captions or Transcript under the finished video.\n\n"
        "No added music, sound effects or B-roll.\n\n"
        "🔒 The existing AI Clipper and AI Video Editor are not used or changed.",
        _master_menu(),
    )


async def handle_master_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        raise ApplicationHandlerStop
    try:
        await query.answer()
    except TelegramError:
        pass
    if not _allowed(update):
        await _safe_edit(query.message, "Master Editor is currently owner-only.", _master_menu())
        raise ApplicationHandlerStop
    action = (query.data or "").split(":", 1)[-1]
    user_id = int(update.effective_user.id) if update.effective_user else 0
    if action=="noop":
        raise ApplicationHandlerStop
    if action=="key_help":
        text=("🔑 Key Command\n\nOwner: /key DAYS COUNT\nExample: /key 1 2\n\n"
              "User activation: /key YOUR_KEY")
        await query.message.reply_text(text)
        raise ApplicationHandlerStop
    if action=="revoke_help":
        if not _owner(update):
            await query.message.reply_text("Only the admin can revoke access keys.")
        else:
            await query.message.reply_text(
                "🚫 Revoke Command\n\n/revoke USER_ID KEY\n"
                "Example: /revoke 123456789 MV-B1-XXXXXXXXXX\n\n"
                "Use /revoke USER_ID to revoke every key redeemed by that user."
            )
        raise ApplicationHandlerStop
    if action.startswith("remove_captions:"):
        token=action.split(":",1)[1]
        clean,transcript,job_dir=_result_paths(context)
        created=float(context.user_data.get("master_result_created") or 0.0)
        valid=(token==context.user_data.get("master_result_token") and clean and job_dir
               and clean.is_file() and time.time()-created<=RESULT_TTL_SECONDS)
        if not valid:
            _clear_result(context,remove_files=True)
            await query.message.reply_text("This caption-free result expired. Create a new Master edit.",reply_markup=_master_menu())
            raise ApplicationHandlerStop
        if user_id in _ACTIVE:
            await query.message.reply_text("A Master job is running. Use Remove Captions after it finishes.")
            raise ApplicationHandlerStop
        if clean.stat().st_size<=MAX_TELEGRAM_OUTPUT_BYTES:
            try:
                await context.bot.send_video(chat_id=query.message.chat.id,video=clean,
                    caption="✅ Captions removed. Voice, cuts, frames, colour and noise cleanup are unchanged.",
                    supports_streaming=True,read_timeout=3600,write_timeout=7200,
                    reply_markup=_result_menu(token,captionless_sent=True,
                        transcript_sent=bool(context.user_data.get("master_transcript_sent"))))
            except TelegramError:
                await context.bot.send_document(chat_id=query.message.chat.id,document=clean,
                    caption="✅ Captions removed. Voice and all other editing are unchanged.",
                    read_timeout=3600,write_timeout=7200,
                    reply_markup=_result_menu(token,captionless_sent=True,
                        transcript_sent=bool(context.user_data.get("master_transcript_sent"))))
            context.user_data["master_captionless_sent"]=True
            try:
                await query.message.edit_reply_markup(_result_menu(token,captionless_sent=True,
                    transcript_sent=bool(context.user_data.get("master_transcript_sent"))))
            except TelegramError:
                pass
        else:
            await query.message.reply_text(
                "✅ Caption-free version is ready, but it exceeds Telegram's delivery limit:\n"+str(clean),
                reply_markup=_master_menu())
        raise ApplicationHandlerStop
    if action.startswith("transcript:"):
        token=action.split(":",1)[1]
        clean,transcript,job_dir=_result_paths(context)
        created=float(context.user_data.get("master_result_created") or 0.0)
        valid=(token==context.user_data.get("master_result_token") and transcript and job_dir
               and transcript.is_file() and time.time()-created<=RESULT_TTL_SECONDS)
        if not valid:
            _clear_result(context,remove_files=True)
            await query.message.reply_text(
                "This transcript expired. Create a new Master edit.",
                reply_markup=_master_menu())
            raise ApplicationHandlerStop
        await context.bot.send_document(
            chat_id=query.message.chat.id,document=transcript,
            filename=transcript.name,caption="📝 Transcript of the delivered edit with timestamps.",
            read_timeout=600,write_timeout=1200,
            reply_markup=_result_menu(token,
                captionless_sent=bool(context.user_data.get("master_captionless_sent")),
                transcript_sent=True))
        context.user_data["master_transcript_sent"]=True
        try:
            await query.message.edit_reply_markup(_result_menu(token,
                captionless_sent=bool(context.user_data.get("master_captionless_sent")),
                transcript_sent=True))
        except TelegramError:
            pass
        raise ApplicationHandlerStop
    if action in {"open","source_full","source_file_range","source_youtube_range","back"} and user_id in _ACTIVE:
        await _safe_edit(query.message,"A Master job is running. Stop it before changing the source method.",_master_back_menu())
        raise ApplicationHandlerStop
    if action == "open":
        _clear_pending(context, remove_files=True)
        context.user_data.pop("mode", None)
        await _show_menu(query.message)
    elif action == "source_full":
        _clear_result(context,remove_files=True)
        _clear_pending(context, remove_files=True)
        context.user_data["mode"] = "master_full_file"
        await _safe_edit(
            query.message,
            "📤 Send Full File\n\n"
            "Upload the exact video as a Telegram video or document. The complete file will be "
            "analyzed, planned, edited and quality-checked. There is no editor duration preset.",
            _master_back_menu(),
        )
    elif action == "source_file_range":
        _clear_result(context,remove_files=True)
        _clear_pending(context, remove_files=True)
        context.user_data["mode"] = "master_file_range_upload"
        await _safe_edit(
            query.message,
            "✂️ File + Timestamp\n\n"
            "Upload the long source video first. After it is received, send the exact range, for example:\n"
            "00:30 - 01:20\n\n"
            "Only that range will enter the new plan-first Master Editor.",
            _master_back_menu(),
        )
    elif action == "source_youtube_range":
        _clear_result(context,remove_files=True)
        _clear_pending(context, remove_files=True)
        context.user_data["mode"] = "master_youtube_range"
        await _safe_edit(
            query.message,
            "🔗 YouTube + Timestamp\n\n"
            "Send the YouTube link and exact range in one message:\n"
            "https://youtu.be/VIDEO_ID  00:30 - 01:20\n\n"
            "The highest available source is used before the new editing plan is built.",
            _master_back_menu(),
        )
    elif action == "stop":
        user_id = int(update.effective_user.id) if update.effective_user else 0
        task = _ACTIVE.get(user_id)
        if not task:
            _clear_pending(context, remove_files=True)
            context.user_data.pop("mode", None)
            await _safe_edit(query.message,"No render was running. Pending Master input was cleared.",_master_menu())
        else:
            task.cancelled = True
            if task.process and task.process.returncode is None:
                try:
                    task.process.terminate()
                except ProcessLookupError:
                    pass
            await _safe_edit(query.message,"Stop requested. The current operation will close safely.",_master_back_menu())
    elif action == "back":
        _clear_pending(context, remove_files=True)
        context.user_data.pop("mode", None)
        if _BOT_MODULE is not None:
            markup = _BOT_MODULE.main_menu(True, True, True)
            footer = _BOT_MODULE.owner_footer() if hasattr(_BOT_MODULE, "owner_footer") else ""
            await _safe_edit(query.message, "🚀 Media Utility Bot Pro\n\nChoose a service below.\n\n" + footer, markup)
    raise ApplicationHandlerStop


async def command_masteredit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        raise ApplicationHandlerStop
    if not _allowed(update):
        await update.message.reply_text("Master Editor is currently owner-only.")
    else:
        await update.message.reply_text(
            "🎞 Master Editor",
            reply_markup=_master_menu(),
        )
    raise ApplicationHandlerStop


def _is_video(message: Any) -> bool:
    if getattr(message, "video", None):
        return True
    document = getattr(message, "document", None)
    if not document:
        return False
    mime = str(getattr(document, "mime_type", "") or "").lower()
    suffix = Path(str(getattr(document, "file_name", "") or "")).suffix.lower()
    return mime.startswith("video/") or suffix in VIDEO_EXTENSIONS


async def _progress_edit(status: Any, text: str, state: Dict[str, Any]) -> None:
    now = time.monotonic()
    if now - float(state.get("last_update") or 0) < 4.0:
        return
    state["last_update"] = now
    await _safe_edit(
        status,
        text,
        InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Stop Master Job", callback_data="master:stop")]]),
    )


async def _run_engine(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    source: Path,
    job_dir: Path,
    status: Any,
    reserved: Optional[ActiveTask] = None,
    options: Optional[dict] = None,
) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    if user.id in _ACTIVE and _ACTIVE[user.id] is not reserved:
        await _safe_edit(status, "A Master Editor job is already running. Stop it first.", _master_menu())
        return
    state = reserved or ActiveTask(user.id)
    _ACTIVE[user.id] = state
    output = job_dir / "MASTER_EDIT_1080P.mp4"
    job_mode = "full"
    progress_state: Dict[str, Any] = {"last_update": 0.0}
    preserve_job = False
    python_bin = os.getenv("MASTER_ENGINE_PYTHON", sys.executable)
    env = os.environ.copy()
    env['MASTER_CAPTION_SCOPE']=str(user.id)
    base = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = base + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # Source receipt starts immediately: captions/layouts are automatic and the
    # only caption choice is the post-render Remove Captions control.
    options=options or {"captions":"auto","split":True,"motion_profile":"smooth"}
    try:
        if state.cancelled:
            await _safe_edit(status, "Master Editor job stopped safely.", _master_menu())
            return
        process = await asyncio.create_subprocess_exec(
            python_bin, "-m", "master_engine", "--input", str(source), "--output", str(output),
            "--mode", job_mode,
            "--captions",options["captions"],"--split","on" if options["split"] else "off",
            "--motion-profile",options["motion_profile"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=base,
            env=env,
        )
        state.process = process
        last_error = ""
        complete_event: Dict[str, Any] = {}
        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            try:
                event = json.loads(line)
            except Exception:
                if line:
                    last_error = line[-800:]
                continue
            if event.get("type") == "progress":
                percent = int(float(event.get("fraction") or 0.0) * 100)
                detail = str(event.get("detail") or "Working")
                stage = str(event.get("stage") or "editing").replace("_", " ").title()
                await _progress_edit(
                    status,
                    f"🎞 Master Editor\n\n{stage}: {percent}%\n{detail}",
                    progress_state,
                )
            elif event.get("type") == "error":
                last_error = str(event.get("error") or "Unknown Master Editor error")
            elif event.get("type") == "complete":
                complete_event = dict(event)
        code = await process.wait()
        if state.cancelled:
            await _safe_edit(status, "Master Editor job stopped safely.", _master_menu())
            return
        output_paths = [Path(value) for value in list(complete_event.get("outputs") or [])]
        captionless_path=Path(str(complete_event.get("captionless_output") or
                                  output.with_name(output.stem+"_NO_CAPTIONS"+output.suffix)))
        transcript_path=Path(str(complete_event.get("transcript") or
                                 output.with_suffix(".transcript.txt")))
        report_path = Path(str(complete_event.get("report") or output.with_suffix(".master_report.json")))
        plan_path = Path(str(complete_event.get("plan") or output.with_suffix(".master_plan.json")))
        if (code != 0 or not output_paths or not captionless_path.is_file()
                or not transcript_path.is_file()):
            raise RuntimeError(last_error or f"Master Editor process exited with code {code}")
        preserve_job = True  # Keep a valid render if ANY Telegram upload fails.
        summary = {}
        if report_path.exists():
            try:
                summary = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                summary = {}
        analysis = dict(summary.get("analysis") or {})
        await _safe_edit(status, "✅ Plan-first render and final quality review passed. Uploading result…")
        caption = telegram_caption(analysis)
        oversized = False
        token=uuid.uuid4().hex[:12]
        result_markup=_result_menu(token)
        context.user_data.update({"master_result_token":token,
            "master_captionless_output":str(captionless_path),
            "master_transcript_output":str(transcript_path),
            "master_result_job_dir":str(job_dir),"master_result_created":time.time(),
            "master_captionless_sent":False,"master_transcript_sent":False})
        asyncio.create_task(_expire_result(context,token))
        for index, result_path in enumerate(output_paths, 1):
            result_caption = caption
            if result_path.exists() and result_path.stat().st_size <= MAX_TELEGRAM_OUTPUT_BYTES:
                try:
                    await context.bot.send_video(
                        chat_id=chat.id, video=result_path, caption=result_caption,
                        supports_streaming=True, read_timeout=3600, write_timeout=7200,
                        reply_markup=result_markup,
                    )
                except TelegramError:
                    await context.bot.send_document(
                        chat_id=chat.id, document=result_path, caption=result_caption,
                        read_timeout=3600, write_timeout=7200,
                        reply_markup=result_markup,
                    )
            else:
                oversized = True
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=result_caption + f"\n\nThe output exceeds the Telegram delivery limit and remains on the server:\n{result_path}",
                    reply_markup=result_markup,
                )
        if os.getenv("MASTER_SEND_REPORTS","0")=="1" and report_path.exists():
            await context.bot.send_document(
                chat_id=chat.id,
                document=report_path,
                caption="Master Editor quality and editorial report",
                read_timeout=600,
                write_timeout=1200,
            )
        if os.getenv("MASTER_SEND_REPORTS","0")=="1" and plan_path.exists():
            await context.bot.send_document(
                chat_id=chat.id,
                document=plan_path,
                caption="Plan created and validated before rendering",
                read_timeout=600,
                write_timeout=1200,
            )
        await _safe_edit(status,
            "✅ Master Editor complete. Remove Captions and Transcript are under the delivered video.",
            _master_menu())
        diagnostic_dir=WORK_DIR/"reports"
        diagnostic_dir.mkdir(parents=True,exist_ok=True)
        for diagnostic in (report_path,plan_path,transcript_path):
            if diagnostic.exists():
                shutil.copy2(diagnostic,diagnostic_dir/(job_dir.name+"_"+diagnostic.name))
        preserve_job = True
    except Exception as exc:
        retained = f"\n\nThe rendered files are retained on the server:\n{job_dir}" if preserve_job else ""
        await _safe_edit(status, "❌ Master Editor could not complete the job.\n\n" + str(exc)[:1000]+retained, _master_menu())
    finally:
        _ACTIVE.pop(user.id, None)
        # Keep an oversized result because its server path was given to the user.
        if not preserve_job:
            shutil.rmtree(job_dir, ignore_errors=True)


async def handle_master_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = str(context.user_data.get("mode") or "")
    if mode not in {"master_full_file", "master_file_range_upload"}:
        return
    if not update.message or not update.effective_user:
        raise ApplicationHandlerStop
    if not _allowed(update):
        await update.message.reply_text("Master Editor is currently owner-only.")
        raise ApplicationHandlerStop
    if not _is_video(update.message):
        await update.message.reply_text("Upload a supported video file as a Telegram video or document.")
        raise ApplicationHandlerStop
    if update.effective_user.id in _ACTIVE:
        await update.message.reply_text("A Master Editor job is already running. Stop it before sending another source.")
        raise ApplicationHandlerStop
    media = update.message.video or update.message.document
    size = int(getattr(media, "file_size", 0) or 0)
    if size > MAX_INPUT_BYTES:
        await update.message.reply_text(
            f"Input exceeds the configured {MAX_INPUT_BYTES / 1024**3:.1f} GiB file limit. "
            "The engine itself has no duration preset."
        )
        raise ApplicationHandlerStop
    _clear_pending(context, remove_files=True)
    # Reserve synchronously: the host bot processes concurrent updates.
    state = ActiveTask(update.effective_user.id)
    _ACTIVE[update.effective_user.id] = state
    status = update.message
    job_dir = WORK_DIR / f"telegram_{update.effective_user.id}_{uuid.uuid4().hex[:10]}"
    original = str(getattr(update.message.document, "file_name", "") or "source.mp4")
    suffix = Path(original).suffix.lower() if Path(original).suffix.lower() in VIDEO_EXTENSIONS else ".mp4"
    source = job_dir / f"source{suffix}"
    try:
        job_dir.mkdir(parents=True, exist_ok=False)
        status = await update.message.reply_text("📥 Receiving the source without using the existing editor…")
        tg_file = await media.get_file()
        await tg_file.download_to_drive(custom_path=source)
        if state.cancelled:
            raise RuntimeError("Master input download was cancelled")
    except Exception as exc:
        _ACTIVE.pop(update.effective_user.id,None)
        shutil.rmtree(job_dir, ignore_errors=True)
        await _safe_edit(status, "Could not receive the source file: " + str(exc)[:500], _master_menu())
        raise ApplicationHandlerStop

    if mode == "master_file_range_upload":
        try:
            source_info = await asyncio.to_thread(probe, source)
            if state.cancelled:
                raise RuntimeError("Master input preparation was cancelled")
        except Exception as exc:
            _ACTIVE.pop(update.effective_user.id,None)
            shutil.rmtree(job_dir, ignore_errors=True)
            await _safe_edit(status, "The uploaded file could not be read: " + str(exc)[:500], _master_menu())
            raise ApplicationHandlerStop
        context.user_data["master_pending_source"] = str(source)
        context.user_data["master_pending_job_dir"] = str(job_dir)
        context.user_data["master_pending_created"] = time.time()
        context.user_data["master_pending_name"] = original
        context.user_data["mode"] = "master_file_range_wait"
        _ACTIVE.pop(update.effective_user.id,None)
        await _safe_edit(
            status,
            "✅ Source received for File + Timestamp.\n\n"
            f"Duration: {_format_time(source_info.duration)}\n"
            "Now send the exact range, for example:\n00:30 - 01:20",
            _master_back_menu(),
        )
        raise ApplicationHandlerStop

    context.user_data.pop("mode",None)
    await _run_engine(update,context,source,job_dir,status,state)
    raise ApplicationHandlerStop


async def handle_master_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = str(context.user_data.get("mode") or "")
    if mode not in {"master_file_range_wait", "master_youtube_range"}:
        return
    if not update.message or not update.effective_user:
        raise ApplicationHandlerStop
    if not _allowed(update):
        await update.message.reply_text("Master Editor is currently owner-only.")
        raise ApplicationHandlerStop

    if update.effective_user.id in _ACTIVE:
        await update.message.reply_text("A Master Editor job is already running. Stop it first.")
        raise ApplicationHandlerStop

    if mode == "master_file_range_wait":
        start, end = parse_timestamp_range(update.message.text or "")
        source, job_dir = _pending_paths(context)
        created = float(context.user_data.get("master_pending_created") or 0.0)
        if not source or not job_dir or not source.exists() or time.time() - created > 6 * 3600:
            _clear_pending(context, remove_files=True)
            context.user_data.pop("mode", None)
            await update.message.reply_text("The pending source expired. Choose File + Timestamp and upload it again.")
            raise ApplicationHandlerStop
        if start is None or end is None or end <= start or end - start < 2.0:
            await update.message.reply_text("Send a valid range of at least 2 seconds, for example: 00:30 - 01:20")
            raise ApplicationHandlerStop
        state = ActiveTask(update.effective_user.id)
        _ACTIVE[update.effective_user.id] = state
        try:
            source_info = await asyncio.to_thread(probe, source)
        except Exception as exc:
            _ACTIVE.pop(update.effective_user.id,None)
            await update.message.reply_text("The pending source could not be read: "+str(exc)[:400])
            raise ApplicationHandlerStop
        if start >= source_info.duration or end > source_info.duration + 0.5:
            _ACTIVE.pop(update.effective_user.id,None)
            await update.message.reply_text(
                f"That range is outside the source duration ({_format_time(source_info.duration)})."
            )
            raise ApplicationHandlerStop
        status = update.message
        ranged = job_dir / "source_range_master.mp4"
        try:
            if state.cancelled:
                raise RuntimeError("Master range preparation was cancelled")
            status = await update.message.reply_text(
                f"✂️ Preparing {_format_time(start)} - {_format_time(end)} at high quality…"
            )
            await asyncio.to_thread(extract_range, source, ranged, start, end, lambda:state.cancelled)
        except Exception as exc:
            _ACTIVE.pop(update.effective_user.id,None)
            await _safe_edit(status, "Could not prepare that range: " + str(exc)[:700], _master_back_menu())
            raise ApplicationHandlerStop
        _clear_pending(context, remove_files=False)
        context.user_data.pop("mode",None)
        await _run_engine(update,context,ranged,job_dir,status,state)
        raise ApplicationHandlerStop

    url, start, end = parse_link_and_range(update.message.text or "")
    if not url or start is None or end is None or end <= start or end - start < 2.0:
        await update.message.reply_text(
            "Send a valid YouTube link and a range of at least 2 seconds in one message.\n"
            "Example: https://youtu.be/VIDEO_ID  00:30 - 01:20"
        )
        raise ApplicationHandlerStop
    if update.effective_user.id in _ACTIVE:
        await update.message.reply_text("A Master Editor job is already running. Stop it first.")
        raise ApplicationHandlerStop
    state = ActiveTask(update.effective_user.id)
    _ACTIVE[update.effective_user.id] = state
    status = update.message
    job_dir = WORK_DIR / f"youtube_{update.effective_user.id}_{uuid.uuid4().hex[:10]}"
    try:
        job_dir.mkdir(parents=True, exist_ok=False)
        status = await update.message.reply_text("🔗 Preparing the selected YouTube range through multi-route transport…")
        ranged = await asyncio.to_thread(
            download_youtube_range,
            url,
            job_dir / "download",
            start,
            end,
            lambda: state.cancelled,
        )
        source_info = await asyncio.to_thread(probe, ranged)
        if source_info.duration <= 0:
            raise RuntimeError("Downloaded YouTube range has no valid duration")
        if state.cancelled:
            raise RuntimeError("Master Editor job cancelled")
    except Exception as exc:
        _ACTIVE.pop(update.effective_user.id, None)
        shutil.rmtree(job_dir, ignore_errors=True)
        await _safe_edit(status, "Could not prepare the YouTube range: " + str(exc)[:800], _master_menu())
        raise ApplicationHandlerStop
    context.user_data.pop("mode",None)
    await _run_engine(update,context,ranged,job_dir,status,state)
    raise ApplicationHandlerStop


async def command_access_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Replace only routing, leaving the legacy /start function body intact."""
    if not update.message:
        raise ApplicationHandlerStop
    context.user_data.pop("mode",None)
    if not _allowed(update):
        await update.message.reply_text(_access_text())
        raise ApplicationHandlerStop
    label="Administrator workspace" if _owner(update) else "Access key active"
    markup=_BOT_MODULE.main_menu(_owner(update),True,True)
    await update.message.reply_text(
        "🎬 Public Media Editing Bot\n\n"
        f"{label}. Choose an editing workflow below.",
        reply_markup=markup,
    )
    raise ApplicationHandlerStop


async def command_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        raise ApplicationHandlerStop
    args=list(context.args or [])
    if _owner(update) and len(args)==2:
        try:
            days=int(args[0]); count=int(args[1])
            if days<1 or days>3650 or count<1 or count>100:
                raise ValueError("DAYS must be 1-3650 and COUNT must be 1-100")
            tokens=_BOT_MODULE.create_access_keys("both",days,count)
        except Exception as exc:
            await update.message.reply_text("Key error: "+str(exc)[:240])
            raise ApplicationHandlerStop
        body=(f"🔑 {len(tokens)} Public Bot Key{'s' if len(tokens)!=1 else ''}\n\n"
              +"\n".join(tokens)+f"\n\nDays: {days}\nCount: {len(tokens)}")
        await update.message.reply_text(body[:4090])
        raise ApplicationHandlerStop
    if len(args)==1:
        try:
            ok,message=_BOT_MODULE.redeem_access_key(update.effective_user.id,args[0])
        except Exception as exc:
            ok,message=False,str(exc)[:240]
        await update.message.reply_text(("✅ " if ok else "❌ ")+message+
            ("\n\nSend /start to open the bot." if ok else ""))
        raise ApplicationHandlerStop
    await update.message.reply_text(
        "Usage:\n"
        "/key YOUR_KEY\n\n"
        "Admin generation:\n/key DAYS COUNT\nExample: /key 1 2"
    )
    raise ApplicationHandlerStop


async def command_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        raise ApplicationHandlerStop
    if not _owner(update):
        await update.message.reply_text("Only the admin can revoke access keys.")
        raise ApplicationHandlerStop
    args=list(context.args or [])
    if len(args) not in {1,2}:
        await update.message.reply_text(
            "Usage:\n/revoke USER_ID KEY\n\n"
            "Or revoke every key redeemed by a user:\n/revoke USER_ID"
        )
        raise ApplicationHandlerStop
    try:
        user_id=int(args[0])
        if user_id<=0:
            raise ValueError("USER_ID must be a positive number")
        requested=args[1].strip().upper() if len(args)==2 else ""
        lock=getattr(_BOT_MODULE,"ACCESS_KEYS_LOCK",None)
        if lock is None:
            raise RuntimeError("access-key lock is unavailable")
        with lock:
            data=_BOT_MODULE._read_access_keys()
            keys=data.setdefault("keys",{})
            if requested:
                item=keys.get(requested)
                if not isinstance(item,dict):
                    raise ValueError("key was not found")
                redeemed=int(item.get("redeemed_by") or 0)
                if redeemed not in {0,user_id}:
                    raise ValueError(f"key belongs to user {redeemed}, not {user_id}")
                selected=[(requested,item)]
            else:
                selected=[(token,item) for token,item in keys.items()
                          if isinstance(item,dict)
                          and int(item.get("redeemed_by") or 0)==user_id]
            scopes=set()
            now=time.time()
            for token,item in selected:
                scope=str(item.get("scope") or "").lower()
                scopes.update(("clipper","editor") if scope=="both" else (scope,))
                if not int(item.get("redeemed_by") or 0):
                    item["redeemed_by"]=-1
                item["revoked_at"]=now
                item["revoked_by"]=int(update.effective_user.id)
            user=data.setdefault("users",{}).setdefault(str(user_id),{})
            for scope in ({"clipper","editor"} if not requested else scopes):
                if scope in {"clipper","editor"}:
                    user[scope]=0
            _BOT_MODULE._write_access_keys(data)
        await update.message.reply_text(
            f"✅ Access revoked\nUser ID: {user_id}\n"
            f"Key records revoked: {len(selected)}"
        )
    except Exception as exc:
        await update.message.reply_text("Revoke error: "+str(exc)[:300])
    raise ApplicationHandlerStop


async def handle_access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _allowed(update):
        return
    text=str(getattr(update.message,"text","") or "").strip().lower()
    command=text.split(None,1)[0].split("@",1)[0] if text.startswith("/") else ""
    if command in {"/redeem","/keystatus","/f","/feedback","/support"}:
        return
    if update.callback_query:
        try:
            await update.callback_query.answer("Access key required.",show_alert=True)
        except TelegramError:
            pass
        if update.callback_query.message:
            await update.callback_query.message.reply_text(_access_text())
    elif update.message:
        await update.message.reply_text(_access_text())
    raise ApplicationHandlerStop


async def _extend_command_menu(application) -> None:
    async def extend(scope, additions):
        try:
            rows=list(await application.bot.get_my_commands(scope=scope))
            by_name={row.command:row for row in rows}
            for command,description in additions:
                by_name[command]=BotCommand(command,description)
            await application.bot.set_my_commands(list(by_name.values()),scope=scope)
        except TelegramError:
            pass
    public=[("key","Activate your public-bot access key")]
    await extend(None,public)
    await extend(BotCommandScopeAllPrivateChats(),public)
    owner_id=int(getattr(_BOT_MODULE,"OWNER_ID",0) or 0)
    if owner_id>0:
        await extend(BotCommandScopeChat(chat_id=owner_id),[
            ("key","Generate keys: /key DAYS COUNT"),
            ("revoke","Revoke: /revoke USER_ID KEY"),
        ])


def install_master_engine(app, bot_module) -> None:
    global _BOT_MODULE
    _BOT_MODULE = bot_module
    broker = getattr(bot_module, "media_transport", None)
    if broker is None:
        raise RuntimeError("Master Editor requires the central media_transport broker")
    bind_broker(broker)
    if app.bot_data.get("_master_edit_engine_installed"):
        return

    original_menu = bot_module.main_menu

    def master_main_menu(*args, **kwargs):
        markup = original_menu(*args, **kwargs)
        rows = [list(row) for row in markup.inline_keyboard]
        if not any(button.callback_data == "master:open" for row in rows for button in row):
            rows.insert(1, [InlineKeyboardButton("🎞 Master Editor", callback_data="master:open")])
        return InlineKeyboardMarkup(rows)

    bot_module.main_menu = master_main_menu
    previous_post_init=getattr(app,"post_init",None)
    async def combined_post_init(application):
        if previous_post_init is not None:
            await previous_post_init(application)
        await _extend_command_menu(application)
    app.post_init=combined_post_init
    app.add_handler(CommandHandler("start",command_access_start),group=-100)
    app.add_handler(CommandHandler("key",command_key),group=-100)
    app.add_handler(CommandHandler("revoke",command_revoke),group=-100)
    app.add_handler(CallbackQueryHandler(handle_access_gate),group=-100)
    app.add_handler(MessageHandler(filters.ALL,handle_access_gate),group=-100)
    app.add_handler(CommandHandler("masteredit", command_masteredit), group=-60)
    app.add_handler(CallbackQueryHandler(
        handle_master_callback,
        pattern=r"^master:",
    ), group=-60)
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL, handle_master_media), group=-60)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_master_text), group=-60)
    app.bot_data["_master_edit_engine_installed"] = True
