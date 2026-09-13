import os,asyncio,re,uuid,logging,aiohttp,aiofiles
import telebot,edge_tts
import google.generativeai as genai
from moviepy.editor import VideoFileClip,AudioFileClip,CompositeVideoClip,concatenate_videoclips,ColorClip,TextClip
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN","")
GEMINI_API_KEY=os.getenv("GEMINI_API_KEY","")
PIXABAY_API_KEY=os.getenv("PIXABAY_API_KEY","")
VIDEO_W,VIDEO_H,SEGMENTS,FPS,VOICE=720,1280,6,24,"ar-EG-SalmaNeural"
logging.basicConfig(level=logging.INFO)
log=logging.getLogger(__name__)
genai.configure(api_key=GEMINI_API_KEY)
bot=telebot.TeleBot(TELEGRAM_TOKEN)
os.makedirs("temp",exist_ok=True)
def generate_script(topic):
    model=genai.GenerativeModel("gemini-1.5-flash")
    prompt=f'اكتب سكريبت فيديو 3 دقايق عن: "{topic}"\nابدأ بـ Hook صادم وقسم النص لـ 6 أجزاء\nأعد الرد بهذا الشكل فقط:\nSEGMENT_1_TEXT: [النص]\nSEGMENT_1_VIDEO: [كلمة انجليزية]\nSEGMENT_2_TEXT: [النص]\nSEGMENT_2_VIDEO: [كلمة]\nSEGMENT_3_TEXT: [النص]\nSEGMENT_3_VIDEO: [كلمة]\nSEGMENT_4_TEXT: [النص]\nSEGMENT_4_VIDEO: [كلمة]\nSEGMENT_5_TEXT: [النص]\nSEGMENT_5_VIDEO: [كلمة]\nSEGMENT_6_TEXT: [النص]\nSEGMENT_6_VIDEO: [كلمة]'
    raw=model.generate_content(prompt).text.strip()
    segments=[]
    for i in range(1,7):
        t=re.search(rf"SEGMENT_{i}_TEXT:\s*(.+?)(?=SEGMENT_{i}_VIDEO:)",raw,re.DOTALL)
        v=re.search(rf"SEGMENT_{i}_VIDEO:\s*(.+?)(?=SEGMENT_{i+1}_TEXT:|$)",raw,re.DOTALL)
        segments.append({"text":t.group(1).strip() if t else f"جزء {i}","video_query":v.group(1).strip() if v else topic})
    return segments
async def _download(url,path,session):
    try:
        async with session.get(url,timeout=aiohttp.ClientTimeout(total=60)) as r:
            if r.status!=200:return False
            async with aiofiles.open(path,"wb") as f:await f.write(await r.read())
        return True
    except:return False
async def _pixabay(query,idx,session):
    try:
        async with session.get("https://pixabay.com/api/videos/",params={"key":PIXABAY_API_KEY,"q":query,"per_page":8,"safesearch":"true"},timeout=aiohttp.ClientTimeout(total=15)) as r:
            hits=(await r.json()).get("hits",[])
        if not hits:return None
        hit=hits[idx%len(hits)]
        for q in("large","medium","small","tiny"):
            v=hit.get("videos",{}).get(q)
            if v and v.get("url"):
                p=f"temp/v{idx}_{uuid.uuid4().hex[:6]}.mp4"
                if await _download(v["url"],p,session):return p
    except:pass
    return None
async def get_video(query,idx,dur,session):
    for q in[query,query.split()[0],"nature"]:
        p=await _pixabay(q,idx,session)
        if p:return p
    p=f"temp/fb{idx}.mp4"
    ColorClip(size=(VIDEO_W,VIDEO_H),color=[(15,15,40),(40,10,10),(10,35,15),(30,15,45),(10,30,40),(45,30,10)][idx%6],duration=dur).write_videofile(p,fps=FPS,logger=None)
    return p
async def get_all_videos(segs,durs):
    async with aiohttp.ClientSession() as s:
        return await asyncio.gather(*[get_video(sg["video_query"],i,durs[i],s) for i,sg in enumerate(segs)])
async def gen_voice(text,idx):
    p=f"temp/a{idx}_{uuid.uuid4().hex[:6]}.mp3"
    await edge_tts.Communicate(text,VOICE).save(p)
    return p
async def gen_voices(segs):
    return await asyncio.gather(*[gen_voice(s["text"],i) for i,s in enumerate(segs)])
def build_seg(vp,ap,text,idx,jid):
    audio=AudioFileClip(ap)
    dur=audio.duration
    try:rv=VideoFileClip(vp,audio=False)
    except:rv=ColorClip(size=(VIDEO_W,VIDEO_H),color=(10,10,20),duration=dur)
    if rv.duration<dur:rv=concatenate_videoclips([rv]*(int(dur/rv.duration)+1))
    rv=rv.subclip(0,dur)
    sc=max(VIDEO_W/rv.w,VIDEO_H/rv.h)
    rs=rv.resize(sc)
    cr=rs.crop(x1=(rs.w-VIDEO_W)/2,y1=(rs.h-VIDEO_H)/2,x2=(rs.w-VIDEO_W)/2+VIDEO_W,y2=(rs.h-VIDEO_H)/2+VIDEO_H)
    oh=int(VIDEO_H*0.38)
    ov=ColorClip(size=(VIDEO_W,oh),color=(0,0,0),duration=dur).set_opacity(0.7).set_position(("center",VIDEO_H-oh))
    words,lines,line=text.split(),[],""
    for w in words:
        t=(line+" "+w).strip()
        if len(t)<=26:line=t
        else:
            if line:lines.append(line)
            line=w
    if line:lines.append(line)
    try:
        tc=TextClip("\n".join(lines[-5:]),fontsize=42,font="DejaVu-Sans-Bold",color="white",stroke_color="black",stroke_width=2,method="caption",size=(VIDEO_W-60,None),align="center").set_duration(dur).set_position(("center",VIDEO_H-oh+15))
        layers=[cr,ov,tc]
    except:layers=[cr,ov]
    out=f"temp/seg{idx}_{jid}.mp4"
    CompositeVideoClip(layers,size=(VIDEO_W,VIDEO_H)).set_audio(audio).write_videofile(out,fps=FPS,codec="libx264",audio_codec="aac",bitrate="2500k",audio_bitrate="192k",threads=4,logger=None)
    return out
def build_final(paths,jid):
    out=f"temp/final_{jid}.mp4"
    concatenate_videoclips([VideoFileClip(p) for p in paths],method="compose").write_videofile(out,fps=FPS,codec="libx264",audio_codec="aac",bitrate="3000k",audio_bitrate="192k",threads=4,logger=None)
    return out
def cleanup(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p):os.remove(p)
        except:pass
@bot.message_handler(commands=["start","help"])
def welcome(m):
    bot.reply_to(m,"🎬 *بوت فيديوهات AI*\n\nأرسل موضوع وهيتعمللك فيديو 3 دقايق!",parse_mode="Markdown")
@bot.message_handler(func=lambda m:True)
def handle(m):
    topic=m.text.strip()
    if len(topic)<3:bot.reply_to(m,"⚠️ اكتب موضوع أطول!");return
    jid=uuid.uuid4().hex[:8]
    pg=bot.reply_to(m,"🔄 *[1/5]* بيكتب السكريبت...",parse_mode="Markdown")
    def upd(t):
        try:bot.edit_message_text(t,m.chat.id,pg.message_id,parse_mode="Markdown")
        except:pass
    tmp=[]
    try:
        segs=generate_script(topic)
        upd("✅ السكريبت جاهز!\n🔄 *[2/5]* بيولّد الأصوات...")
        loop=asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        aps=loop.run_until_complete(gen_voices(segs))
        tmp+=aps
        durs=[AudioFileClip(p).duration for p in aps]
        upd("✅ الأصوات جاهزة!\n🔄 *[3/5]* بيحمّل الفيديوهات...")
        vps=loop.run_until_complete(get_all_videos(segs,durs))
        tmp+=vps
        upd("✅ الفيديوهات جاهزة!\n🔄 *[4/5]* بيجمّع...")
        sps=[]
        for i,(sg,vp,ap) in enumerate(zip(segs,vps,aps)):
            upd(f"🔄 *[4/5]* الجزء {i+1}/6...")
            sp=build_seg(vp,ap,sg["text"],i,jid)
            sps.append(sp)
            tmp.append(sp)
        upd("🔄 *[5/5]* الفيديو النهائي...")
        fp=build_final(sps,jid)
        tmp.append(fp)
        upd("✅ جاهز! بيترفع...")
        sc="\n\n".join([f"[{i+1}] {s['text']}" for i,s in enumerate(segs)])
        with open(fp,"rb") as f:
            bot.send_video(m.chat.id,f,caption=f"🎬 *{topic}*\n\n{sc[:900]}...",parse_mode="Markdown",supports_streaming=True)
        try:bot.delete_message(m.chat.id,pg.message_id)
        except:pass
    except Exception as e:
        log.exception("خطأ")
        upd(f"❌ خطأ:\n`{str(e)[:300]}`")
    finally:cleanup(*tmp)
if __name__=="__main__":
    log.info("🚀 شغّال!")
    bot.infinity_polling(timeout=60,long_polling_timeout=60)
