"""Runtime-safe V7.1 patch for the exact Master Editor V7 package."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

_installed = False


def _patch_composition():
    from . import composition as c
    original_geometry = c.geometry

    def geometry(mode, active_side=None, face_count=0, panel_members=()):
        lead=max(0,face_count-1) if active_side=="right" else 0
        other=0 if lead else max(0,face_count-1)
        panel=c._panel
        W,H=c.W,c.H
        if mode=="reaction_pip":
            return c.Geometry((
                c.Panel(0,0,W,1240,lead,(80,90,920,1030),face_fraction=.42),
                c.Panel(0,1240,W,680,other,(80,45,920,565),face_fraction=.37)),1190)
        if mode in {"listener_context","duo_context"}:
            return c.Geometry((panel(0,0,W,960,0),panel(0,960,W,960,max(0,face_count-1))),960)
        if mode=="cinema_duo":
            return c.Geometry((panel(0,0,W,1040,lead),panel(0,1040,W,880,other)),1020)
        if mode=="offset_duo":
            return c.Geometry((panel(0,0,W,1080,lead),panel(0,1080,W,840,other)),1050)
        if mode=="triple_column":
            return c.Geometry(tuple(panel(n*360,0,360,H,n) for n in range(3)),1220)
        if mode=="hero_strip":
            rest=[index for index in range(3) if index!=lead]
            return c.Geometry((panel(0,0,W,1200,lead),panel(0,1200,540,720,rest[0]),
                               panel(540,1200,540,720,rest[1])),1160)
        if mode=="portrait_card":
            return c.Geometry((panel(0,0,W,H,lead),),1450)
        return original_geometry(mode,active_side,face_count,panel_members)

    c.geometry=geometry
    original_render=c.render_geometry
    black=re.compile(r"^\[([^]]+)\]scale=(\d+):(\d+),drawbox=color=black:t=fill,setsar=1\[([^]]+)\]$")

    def render_geometry(*args,**kwargs):
        lines=original_render(*args,**kwargs)
        fixed=[]
        for line in lines:
            match=black.match(line)
            if not match:
                fixed.append(line)
                continue
            source,width,height,target=match.groups()
            w,h=int(width),int(height)
            fixed.append(f"[{source}]{c._background_scale(w,h)},crop={w}:{h},"
                         f"eq=brightness=-0.06:saturation=0.86,setsar=1[{target}]")
        return fixed
    c.render_geometry=render_geometry


def _patch_captions():
    from . import captions as cp

    def reference_style(style,words,height):
        font=cp._font(style.font,1000,style.italic,style.font_weight)
        factor=cp.ass_metric_scale(cp._font_path(style.font,style.italic,style.font_weight))
        box=font.getbbox("H")
        size=max(12,round(74*height/1920*1000/max(1,(box[3]-box[1])*factor)))
        tokens=[token.upper() if style.uppercase else token
                for word in words for token in word.text.split()]
        while size>12 and any(cp._width(token,style,size)+2*style.outline_size+8>style.max_width
                              for token in tokens):
            size-=1
        return replace(style,size=size,active_peak_scale=106,
                       active_settle_scale=100,normal_scale=100)

    def phrase_zoom(start,end,at,until):
        if end-start<.50:
            return r'\fscx100\fscy100'
        knots=((start,100.0),(start+.14,106.0),(start+.42,100.0))
        def value(moment):
            for (a,x),(b,y) in zip(knots,knots[1:]):
                if moment<=b:
                    return x+(y-x)*max(0,min(1,(moment-a)/(b-a)))
            return 100.0
        at=round(at*100)/100; until=round(until*100)/100
        scale=value(at); tags=rf'\fscx{scale:.3f}\fscy{scale:.3f}'; previous=at
        for stop in sorted({min(until,moment) for moment,_ in knots if moment>at}):
            if stop>previous:
                a,b=round((previous-at)*1000),round((stop-at)*1000)
                tags+=rf'\t({a},{b},\fscx{value(stop):.3f}\fscy{value(stop):.3f})'
                previous=stop
        return tags

    cp._reference_style=reference_style
    cp._phrase_zoom=phrase_zoom


def _patch_catalog():
    from . import caption_catalog as cc
    original=cc.select_style
    extra={
        "health_fitness":{"reference_bold","clean_outline","soft_karaoke","highlight_box","documentary_bold","clean_pop"},
        "product_demo":{"rounded_plate","marker_line","clean_outline","highlight_box","editorial_label","clean_pop"},
        "vlog_travel":{"soft_karaoke","clean_lowercase","story_quote","luxe_serif","fashion_stack","clean_pop"},
        "music_nightlife":{"duotone_pulse","word_focus","double_deck","fashion_stack","neon_minimal","clean_pop"},
        "real_estate":{"clean_outline","editorial_sentence","luxe_serif","light_documentary","boutique_lower","rounded_plate"},
    }

    def select_style(media,profile,transcript,samples,**kwargs):
        style,info=original(media,profile,transcript,samples,**kwargs)
        allowed=set(extra.get(profile.category,()))
        multi=sum(len(row.faces)>=2 for row in samples)/len(samples) if samples else 0.0
        if multi>=.22:
            allowed|={"reference_bold","compact_stack","clean_outline","stack_emphasis",
                      "fashion_stack","clean_pop","word_serif"}
        if allowed and style.family not in allowed:
            pool=[row for row in cc.catalog().values() if row.family in allowed
                  and (row.busy_safe or row.outline_size>=3)]
            if pool:
                seed=str(info.get("media_fingerprint") or transcript.text or media.path)
                style=sorted(pool,key=lambda row:row.name)[int(hashlib.sha256(seed.encode()).hexdigest()[:12],16)%len(pool)]
                info.update(selected_template=style.name,selected_family=style.family,
                            selection_policy="category + conversation match; creator-scoped visible-look rotation")
        info["multi_person_sample_ratio"]=round(multi,3)
        return style,info

    def alternate(primary,selection):
        styles=cc.catalog(); families=set(selection.get("eligible_families") or ())
        rows=[style for style in styles.values()
              if style.name!=primary.name and style.family!=primary.family
              and (not families or style.family in families)
              and (style.font,style.uppercase,style.italic,style.plate,style.phrase_mode)
                 !=(primary.font,primary.uppercase,primary.italic,primary.plate,primary.phrase_mode)]
        if not rows:
            rows=[style for style in styles.values()
                  if style.name!=primary.name and style.family!=primary.family]
        rows=sorted(rows,key=lambda style:style.name)
        seed=str(selection.get("media_fingerprint") or primary.name)+":alternate"
        return rows[int(hashlib.sha256(seed.encode()).hexdigest()[:12],16)%len(rows)]

    cc.select_style=select_style
    cc.select_alternate_style=alternate


def _patch_quality_and_motion():
    from . import config, motion, polish
    original_from_env=config.Settings.from_env

    @classmethod
    def from_env(cls):
        return replace(original_from_env(),crf=14)
    config.Settings.from_env=from_env

    def assessment(media,samples):
        from statistics import median
        sharpness=median(row.sharpness for row in samples) if samples else None
        short_edge=min(media.width,media.height)
        low=short_edge<1080; soft=sharpness is not None and sharpness<.18
        restore=low or soft
        return {"short_edge_pixels":short_edge,
            "median_normalized_sharpness":None if sharpness is None else round(sharpness,3),
            "low_resolution":low,"soft_source":soft,"restoration_applied":restore,
            "mode":"adaptive_restore" if restore else "native_detail_preserved",
            "method":("mild temporal/spatial denoise + edge-limited sharpen + Lanczos scaling"
                      if restore else "measured colour balance + light edge-limited sharpen")}
    polish.quality_assessment=assessment
    original_colour=polish.colour_profile
    def colour(media,samples,enabled=True):
        value=original_colour(media,samples,enabled)
        if enabled and assessment(media,samples)["restoration_applied"]:
            value=replace(value,sharpen=.40,denoise=.72,restoration_mode="adaptive_restore")
        return value
    polish.colour_profile=colour

    original_profile=motion.profile_values
    @lru_cache(maxsize=2)
    def profile(name):
        return (.16,3200.0) if name!="xml_reference" else original_profile(name)
    motion.profile_values=profile


def _patch_engine():
    from . import caption_catalog as cc
    from . import engine as eng
    from . import render as rnd

    def write_sidecar(plan,path,settings,style=None):
        words=rnd.map_words(plan.transcript.words,plan.spans) if settings.captions_enabled else []
        layouts=rnd._map_layouts_to_output(plan.layouts,plan.spans)
        graphics=rnd._map_graphics_to_output(plan.graphics,plan.spans)
        path.parent.mkdir(parents=True,exist_ok=True)
        if words or graphics:
            rnd.write_ass(words,layouts,style or plan.caption_style,path,
                          width=settings.width,height=settings.height,graphics=graphics)
        else:
            path.write_text("",encoding="utf-8")
        return path
    rnd.write_caption_sidecar=write_sidecar

    original_build=eng.MasterEngine._build_plan
    def build(self,*args,**kwargs):
        plan=original_build(self,*args,**kwargs)
        selection=dict(plan.analysis.get("caption_selection") or {})
        alternate=cc.select_alternate_style(plan.caption_style,selection)
        quality=eng.quality_assessment(plan.media,[])
        plan.analysis.update({"engine_version":"7.1.0",
            "alternate_caption_style":alternate.name,
            "caption_sizing":"fixed large base size; controlled 100-106-100 percent phrase pulse",
            "canvas_policy":"portrait-source cover" if .50<=plan.media.aspect<=.68 else "podcast-reference cover",
            "canvas_policy_locked_for_entire_video":True,"black_gutters_allowed":False,
            "multi_panel_quality":"native crop -> Lanczos scale -> CRF 14 Full HD; measured weak-source restoration",
            "post_render_change_captions":True,
            "source_quality_warning":"none" if not quality["low_resolution"] else
                "source is below the 1080p delivery short edge; adaptive restoration is applied without inventing detail"})
        return plan
    eng.MasterEngine._build_plan=build

    original_render=eng.render
    def render(plan,output,work,settings,**kwargs):
        result=original_render(plan,output,work,settings,**kwargs)
        name=str(plan.analysis.get("alternate_caption_style") or "")
        style=cc.catalog().get(name) or cc.select_alternate_style(
            plan.caption_style,dict(plan.analysis.get("caption_selection") or {}))
        write_sidecar(plan,Path(output).with_suffix(".alternate_captions.ass"),settings,style)
        return result
    eng.render=render

    original_job=eng.MasterEngine.run_job
    def run_job(self,input_path,output_path,**kwargs):
        alternate=Path(output_path).resolve().with_suffix(".alternate_captions.ass")
        try:
            result=original_job(self,input_path,output_path,**kwargs)
            if self.settings.captions_enabled and self.settings and not alternate.is_file():
                raise eng.MasterEngineError("Alternate caption sidecar was not created")
            result["alternate_caption_ass"]=alternate
            report=Path(result["report"])
            if report.is_file():
                data=json.loads(report.read_text(encoding="utf-8"))
                data["engine"]="Master Editor 7.1.0"
                data["alternate_caption_ass"]=str(alternate)
                eng.write_json(report,data)
            return result
        except Exception:
            alternate.unlink(missing_ok=True)
            raise
    eng.MasterEngine.run_job=run_job


def _patch_telegram():
    try:
        from . import telegram_bridge as tb
        from telegram import BotCommand,BotCommandScopeAllPrivateChats,BotCommandScopeChat,InlineKeyboardButton,InlineKeyboardMarkup
        from telegram.error import TelegramError
        from telegram.ext import ApplicationHandlerStop,CommandHandler
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("telegram"):
            return
        raise
    from .fonts import FONT_DIR
    from .utils import filter_escape

    def access_text():
        return ("🤖 You Can Use Public Bot\n\n"
            "If you want to use the private editor, please contact the admin.\n\n"
            "If you already have an access key, activate it with:\n/redeem YOUR_KEY")
    def access_menu():
        admin=str(getattr(tb._BOT_MODULE,"OWNER_USERNAME","") or "").strip().lstrip("@")
        reader=getattr(tb._BOT_MODULE,"_read_public_bot_username",None)
        try: public=str(reader() if callable(reader) else "").strip().lstrip("@")
        except Exception: public=""
        buttons=[]
        if public: buttons.append(InlineKeyboardButton("🤖 Public Bot",url=f"https://t.me/{public}"))
        if admin: buttons.append(InlineKeyboardButton("💬 Contact Admin",url=f"https://t.me/{admin}"))
        return InlineKeyboardMarkup([buttons]) if buttons else None
    tb._access_text=access_text; tb._public_access_menu=access_menu

    async def access_start(update,context):
        if not update.message: raise ApplicationHandlerStop
        context.user_data.pop("mode",None)
        if not tb._allowed(update):
            await update.message.reply_text(access_text(),reply_markup=access_menu())
            raise ApplicationHandlerStop
        label="Administrator workspace" if tb._owner(update) else "Access key active"
        await update.message.reply_text("🎬 Public Media Editing Bot\n\n"+label+
            ". Choose an editing workflow below.",reply_markup=tb._BOT_MODULE.main_menu(tb._owner(update),True,True))
        raise ApplicationHandlerStop

    async def key_command(update,context):
        if not update.message or not update.effective_user: raise ApplicationHandlerStop
        if not tb._owner(update):
            await update.message.reply_text("Activate your access key with:\n/redeem YOUR_KEY")
            raise ApplicationHandlerStop
        args=list(context.args or [])
        if len(args)==2:
            try:
                days,count=int(args[0]),int(args[1])
                if not 1<=days<=3650 or not 1<=count<=100: raise ValueError("DAYS 1-3650; COUNT 1-100")
                tokens=tb._BOT_MODULE.create_access_keys("both",days,count)
                await update.message.reply_text((f"🔑 {len(tokens)} Public Bot Key(s)\n\n"+
                    "\n".join(tokens)+f"\n\nDays: {days}\nCount: {len(tokens)}")[:4090])
            except Exception as exc: await update.message.reply_text("Key error: "+str(exc)[:240])
        else:
            await update.message.reply_text("Admin: /key DAYS COUNT\nExample: /key 2 1\n\nUser: /redeem YOUR_KEY")
        raise ApplicationHandlerStop

    async def revoke(update,context):
        if not update.message or not update.effective_user: raise ApplicationHandlerStop
        if not tb._owner(update):
            await update.message.reply_text("Only the admin can revoke access keys."); raise ApplicationHandlerStop
        if len(context.args or [])!=1:
            await update.message.reply_text("Usage: /revoke USER_ID"); raise ApplicationHandlerStop
        try:
            user_id=int(context.args[0]); now=time.time()
            if user_id<=0: raise ValueError("positive USER_ID required")
            with tb._BOT_MODULE.ACCESS_KEYS_LOCK:
                data=tb._BOT_MODULE._read_access_keys(); keys=data.setdefault("keys",{})
                selected=[item for item in keys.values() if isinstance(item,dict)
                          and int(item.get("redeemed_by") or 0)==user_id]
                for item in selected: item.update(revoked_at=now,revoked_by=int(update.effective_user.id))
                state=data.setdefault("users",{}).setdefault(str(user_id),{})
                state["clipper"]=state["editor"]=0; tb._BOT_MODULE._write_access_keys(data)
            await update.message.reply_text(f"✅ Access revoked\nUser ID: {user_id}\nKey records revoked: {len(selected)}")
        except Exception as exc: await update.message.reply_text("Revoke error: "+str(exc)[:300])
        raise ApplicationHandlerStop

    async def listkey(update,context):
        if not update.message or not update.effective_user: raise ApplicationHandlerStop
        if not tb._owner(update):
            await update.message.reply_text("Only the admin can view access keys."); raise ApplicationHandlerStop
        data=tb._BOT_MODULE._read_access_keys(); now=time.time(); active=[]
        for raw,state in data.get("users",{}).items():
            if not isinstance(state,dict): continue
            try: uid=int(raw); expiry=max(float(state.get("clipper") or 0),float(state.get("editor") or 0))
            except (TypeError,ValueError): continue
            if uid>0 and expiry>now:
                tokens=[token for token,item in data.get("keys",{}).items()
                        if isinstance(item,dict) and int(item.get("redeemed_by") or 0)==uid]
                active.append((uid,expiry,tokens))
        lines=[f"🔑 Active key users: {len(active)}",""]
        for uid,expiry,tokens in sorted(active):
            lines += [f"User ID: {uid} | {max(1,int((expiry-now+86399)//86400))} day(s) left",
                      "Key: "+(", ".join(tokens) if tokens else "record unavailable"),""]
        if not active: lines.append("No users currently have active key access.")
        text="\n".join(lines)
        for start in range(0,len(text),3900): await update.message.reply_text(text[start:start+3900])
        raise ApplicationHandlerStop

    async def gate(update,context):
        if tb._allowed(update): return
        raw=str(getattr(update.message,"text","") or "").strip().lower()
        command=raw.split(None,1)[0].split("@",1)[0] if raw.startswith("/") else ""
        if command in {"/redeem","/keystatus","/f","/feedback","/support"}: return
        if update.callback_query:
            try: await update.callback_query.answer("Access key required.",show_alert=True)
            except TelegramError: pass
            if update.callback_query.message:
                await update.callback_query.message.reply_text(access_text(),reply_markup=access_menu())
        elif update.message: await update.message.reply_text(access_text(),reply_markup=access_menu())
        raise ApplicationHandlerStop

    async def command_menu(application):
        async def set_scope(scope,additions,hidden=()):
            try:
                rows=list(await application.bot.get_my_commands(scope=scope)); hidden=set(hidden)
                values={row.command:row for row in rows if row.command not in hidden}
                for name,description in additions: values[name]=BotCommand(name,description)
                await application.bot.set_my_commands(list(values.values()),scope=scope)
            except TelegramError: pass
        hidden=("key","revoke","listkey")
        await set_scope(None,[("redeem","Redeem an access key")],hidden)
        await set_scope(BotCommandScopeAllPrivateChats(),[("redeem","Redeem an access key")],hidden)
        owner=int(getattr(tb._BOT_MODULE,"OWNER_ID",0) or 0)
        if owner:
            await set_scope(BotCommandScopeChat(chat_id=owner),[("key","Generate: /key DAYS COUNT"),
                ("revoke","Revoke every key for a user ID"),("listkey","List active key users and keys")])

    def result_menu(token,captionless_sent=False,transcript_sent=False,caption_changed=False):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Caption Changed" if caption_changed else "🎨 Change Caption",
                callback_data="master:noop" if caption_changed else f"master:change_captions:{token}"),
             InlineKeyboardButton("✅ Caption-free Sent" if captionless_sent else "🚫 Remove Caption",
                callback_data="master:noop" if captionless_sent else f"master:remove_captions:{token}")],
            [InlineKeyboardButton("✅ Transcript Sent" if transcript_sent else "📝 Transcript",
                callback_data="master:noop" if transcript_sent else f"master:transcript:{token}")],
            [InlineKeyboardButton("🎞 New Master Edit",callback_data="master:open")]])

    original_safe_edit=tb._safe_edit
    async def safe_edit(message,text,markup=None):
        text=text.replace("Remove Captions and Transcript are under the delivered video.",
                          "Change Caption, Remove Caption and Transcript are under the delivered video.")
        return await original_safe_edit(message,text,markup)
    tb._safe_edit=safe_edit

    original_callback=tb.handle_master_callback
    async def callback(update,context):
        query=update.callback_query
        action=(query.data or "").split(":",1)[-1] if query else ""
        if action in {"key_help","revoke_help"}:
            if query and query.message:
                try: await query.answer()
                except TelegramError: pass
                await query.message.reply_text("This old menu action is no longer available.")
            raise ApplicationHandlerStop
        if not action.startswith("change_captions:"):
            return await original_callback(update,context)
        if not query or not query.message: raise ApplicationHandlerStop
        try: await query.answer()
        except TelegramError: pass
        if not tb._allowed(update):
            await query.message.reply_text(access_text(),reply_markup=access_menu()); raise ApplicationHandlerStop
        token=action.split(":",1)[1]; uid=int(update.effective_user.id) if update.effective_user else 0
        job_raw=context.user_data.get("master_result_job_dir"); clean_raw=context.user_data.get("master_captionless_output")
        job=Path(str(job_raw)) if job_raw else None; clean=Path(str(clean_raw)) if clean_raw else None
        ass=job/"MASTER_EDIT_1080P.alternate_captions.ass" if job else None
        output=job/"MASTER_EDIT_CHANGED_CAPTION.mp4" if job else None
        created=float(context.user_data.get("master_result_created") or 0)
        if not (token==context.user_data.get("master_result_token") and clean and ass and output
                and clean.is_file() and ass.is_file() and ass.stat().st_size>100
                and time.time()-created<=tb.RESULT_TTL_SECONDS):
            await query.message.reply_text("This caption option expired or has no spoken transcript. Create a new Master edit.",reply_markup=tb._master_menu())
            raise ApplicationHandlerStop
        if uid in tb._ACTIVE:
            await query.message.reply_text("A Master job is already running. Wait for it to finish."); raise ApplicationHandlerStop
        state=tb.ActiveTask(uid); tb._ACTIVE[uid]=state
        notice=await query.message.reply_text("🎨 Applying the alternate video-matched caption template…")
        try:
            if not output.is_file():
                vf="format=pix_fmts=yuv420p,subtitles='"+filter_escape(ass)+"':fontsdir='"+filter_escape(FONT_DIR)+"'"
                command=["ffmpeg","-nostdin","-y","-hide_banner","-loglevel","error","-filter_threads","1",
                    "-threads:v","2","-i",str(clean),"-map","0:v:0","-map","0:a:0?","-vf",vf,
                    "-c:v","libx264","-preset","fast","-crf","14","-profile:v","high","-pix_fmt","yuv420p",
                    "-colorspace","bt709","-color_primaries","bt709","-color_trc","bt709","-threads:v","2",
                    "-c:a","copy","-movflags","+faststart",str(output)]
                process=await asyncio.create_subprocess_exec(*command,stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT); state.process=process; raw,_=await process.communicate()
                if state.cancelled or process.returncode:
                    output.unlink(missing_ok=True)
                    raise RuntimeError("stopped" if state.cancelled else raw.decode("utf-8","replace")[-500:])
            a,b=await asyncio.gather(asyncio.to_thread(tb.probe,clean),asyncio.to_thread(tb.probe,output))
            if b.width!=1080 or b.height!=1920 or abs(a.duration-b.duration)>.45:
                output.unlink(missing_ok=True); raise RuntimeError("Full HD/timing verification failed")
            markup=result_menu(token,bool(context.user_data.get("master_captionless_sent")),
                               bool(context.user_data.get("master_transcript_sent")),True)
            if output.stat().st_size<=tb.MAX_TELEGRAM_OUTPUT_BYTES:
                try: await context.bot.send_video(chat_id=query.message.chat.id,video=output,
                    caption="✅ Caption template changed. All other editing is unchanged.",supports_streaming=True,
                    read_timeout=3600,write_timeout=7200,reply_markup=markup)
                except TelegramError: await context.bot.send_document(chat_id=query.message.chat.id,document=output,
                    caption="✅ Caption template changed. All other editing is unchanged.",read_timeout=3600,
                    write_timeout=7200,reply_markup=markup)
            else: await query.message.reply_text("Changed-caption version is ready but exceeds Telegram's limit:\n"+str(output),reply_markup=markup)
            context.user_data["master_caption_changed"]=True
            await tb._safe_edit(notice,"✅ Alternate caption version ready.")
        except Exception as exc:
            await tb._safe_edit(notice,"❌ Could not change captions: "+str(exc)[:700],result_menu(token))
        finally: tb._ACTIVE.pop(uid,None)
        raise ApplicationHandlerStop

    tb.command_access_start=access_start; tb.command_key=key_command; tb.command_revoke=revoke
    tb.command_listkey=listkey; tb.handle_access_gate=gate; tb._extend_command_menu=command_menu
    tb._result_menu=result_menu; tb.handle_master_callback=callback
    original_install=tb.install_master_engine
    def install(app,bot_module):
        original_install(app,bot_module)
        current=bot_module.main_menu
        def clean_menu(*args,**kwargs):
            markup=current(*args,**kwargs)
            rows=[[button for button in row if button.callback_data not in {"master:key_help","master:revoke_help"}]
                  for row in markup.inline_keyboard]
            return InlineKeyboardMarkup([row for row in rows if row])
        bot_module.main_menu=clean_menu
        app.add_handler(CommandHandler("listkey",listkey),group=-100)
    tb.install_master_engine=install


def install():
    global _installed
    if _installed: return
    _installed=True
    _patch_composition()
    _patch_captions()
    _patch_catalog()
    _patch_quality_and_motion()
    _patch_engine()
    _patch_telegram()
