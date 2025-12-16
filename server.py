import os
import uuid
import asyncio
import subprocess
import shlex
from fastapi import FastAPI, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

app = FastAPI()
os.makedirs("renders", exist_ok=True)
os.makedirs("/tmp", exist_ok=True)

jobs = {}
queue = asyncio.Queue()

app.mount("/", StaticFiles(directory="static", html=True), name="static")
app.mount("/renders", StaticFiles(directory="renders"), name="renders")


@app.post("/render")
async def render_endpoint(
    image: UploadFile,
    animation: str = Form(...),
    speed: float = Form(1.0)
):
    # validate
    if image.content_type.split('/')[0] != 'image':
        raise HTTPException(status_code=400, detail="Arquivo não é imagem")

    job_id = str(uuid.uuid4())
    tmp_in = f"/tmp/{job_id}_in"
    tmp_crop = f"/tmp/{job_id}_crop.jpg"
    out_path = f"renders/{job_id}.mp4"

    # salvar upload temporário
    with open(tmp_in, "wb") as f:
        f.write(await image.read())

    # crop central 16:9 com Pillow
    try:
        im = Image.open(tmp_in)
        iw, ih = im.size
        target_ratio = 16/9

        if (iw / ih) > target_ratio:
            # imagem muito larga -> cortar largura
            new_h = ih
            new_w = int(ih * target_ratio)
        else:
            # imagem muito alta -> cortar altura
            new_w = iw
            new_h = int(iw / target_ratio)

        left = (iw - new_w) // 2
        top = (ih - new_h) // 2
        im_crop = im.crop((left, top, left + new_w, top + new_h))
        # salvar como jpeg para ffmpeg
        im_crop.save(tmp_crop, format="JPEG", quality=95)
    except Exception as e:
        # limpeza e resposta de erro
        if os.path.exists(tmp_in): os.remove(tmp_in)
        raise HTTPException(status_code=500, detail=f"Erro ao processar imagem: {e}")

    # criar job e enfileirar
    jobs[job_id] = {"progress": 0, "done": False, "url": None, "error": None}
    await queue.put((job_id, tmp_crop, out_path, animation, float(speed)))

    return JSONResponse({"id": job_id})


@app.get("/progress/{job_id}")
def progress(job_id: str):
    data = jobs.get(job_id)
    if not data:
        return JSONResponse(status_code=404, content={"detail": "Job não encontrado"})
    return data


async def run_ffmpeg_with_progress(cmd, job_id):
    """
    Executa ffmpeg (list form) com -progress pipe:1 e atualiza jobs[job_id]['progress'].
    Retorna True se sucesso.
    """
    # adiciona -progress pipe:1 para pegar percentuais
    # OBS: nem todas as builds do ffmpeg emitem percent -> faremos fallback
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    # parse basic progress by reading lines and looking for "frame" or "time" or "progress"
    last_percent = 0
    try:
        while True:
            line = process.stdout.readline()
            if line == '' and process.poll() is not None:
                break
            if not line:
                await asyncio.sleep(0.01)
                continue
            line = line.strip()
            # opcional: parse "frame=...", "time=00:00:02.00", "progress=..." etc.
            # vamos tentar estimar a % a partir de "time=" quando disponível
            if line.startswith("time="):
                # time=HH:MM:SS.msec
                time_str = line.split("=",1)[1]
                # não temos duração aqui; o comando passará -t duration então fallback
                # ignorar
                pass
            if "progress=" in line:
                # progress=end or continue
                if "progress=end" in line:
                    jobs[job_id]["progress"] = 100
            # update some heartbeat
            if process.poll() is None:
                last_percent = min(99, last_percent + 1)
                jobs[job_id]["progress"] = last_percent
        rc = process.poll()
        return rc == 0
    except Exception as e:
        process.kill()
        raise e


async def worker():
    while True:
        job_id, img_path, out_path, anim, speed = await queue.get()
        try:
            jobs[job_id]["progress"] = 5

            fps = 30
            base_duration = 6.0
            duration = max(2.0, base_duration / max(0.0001, speed))

            # construir filtro zoompan com duração e fps consistente
            # zoompan expects expressions in terms of 'on' or 't' depending; we'll implement using zoompan with d=frames
            total_frames = int(fps * duration)

            # map animations to zoompan expressions (using x/y/zoom expressions)
            # We'll use ffmpeg filter zoompan with 'z' and 'x' and 'y' as functions of 'on' (frame index)
            # on goes from 0..d-1. We'll compute normalized t = on/(d-1)
            d = total_frames
            # prepare expressions using 'on' variable
            # convert t = on/(d-1)
            t_expr = f"on/{max(1,d-1)}"

            if anim == "lr":
                x_expr = f"({t_expr})*(iw-{d}*0)"  # we'll use simpler approach below (use overlay)
                # simpler approach: use crop + x expression using 'iw' and 'in_w', but different ffmpeg versions vary
                # We'll instead use generic scale and overlay: use zoompan with x and d
                vf = f"zoompan=d={d}:s=1280x720:fps={fps}:x='(in_w-w)/2*t/{d}':y='(in_h-h)/2'"
            elif anim == "rl":
                vf = f"zoompan=d={d}:s=1280x720:fps={fps}:x='(in_w-w)/2*(1 - t/{d})':y='(in_h-h)/2'"
            elif anim == "tb":
                vf = f"zoompan=d={d}:s=1280x720:fps={fps}:x='(in_w-w)/2':y='(in_h-h)/2*t/{d}'"
            elif anim == "bt":
                vf = f"zoompan=d={d}:s=1280x720:fps={fps}:x='(in_w-w)/2':y='(in_h-h)/2*(1 - t/{d})'"
            elif anim == "zoomIn":
                # zoom from 1.0 to 1.25
                vf = f"zoompan=d={d}:s=1280x720:fps={fps}:z='1+0.25*t/{d}',x='(in_w/2)-(w/2)',y='(in_h/2)-(h/2)'"
            elif anim == "zoomOut":
                vf = f"zoompan=d={d}:s=1280x720:fps={fps}:z='1.25-0.25*t/{d}',x='(in_w/2)-(w/2)',y='(in_h/2)-(h/2)'"
            elif anim == "panH":
                vf = f"zoompan=d={d}:s=1280x720:fps={fps}:x='(in_w-w)*(t/{d})',y='(in_h-h)/2'"
            elif anim == "panV":
                vf = f"zoompan=d={d}:s=1280x720:fps={fps}:x='(in_w-w)/2',y='(in_h-h)*(t/{d})'"
            elif anim == "cinematic":
                vf = f"zoompan=d={d}:s=1280x720:fps={fps}:z='1+0.15*t/{d}':x='(in_w-w)*(t/{d})':y='(in_h-h)*(1 - t/{d})'"
            else:
                vf = f"zoompan=d={d}:s=1280x720:fps={fps}:x='(in_w-w)/2':y='(in_h-h)/2'"

            # ffmpeg command: loop a single image and apply zoompan, then encode
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", img_path,
                "-vf", vf,
                "-t", str(duration),
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                out_path
            ]

            jobs[job_id]["progress"] = 10

            # run ffmpeg and update progress with heartbeat fallback
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

            # simplistic progress estimator: parse elapsed time from output or heartbeat increment
            import time as _time
            last_update = _time.time()
            percent = 10
            while True:
                line = proc.stdout.readline()
                if line == "" and proc.poll() is not None:
                    break
                if not line:
                    await asyncio.sleep(0.05)
                    continue
                l = line.strip()
                # try to find "time=" in ffmpeg output
                if "time=" in l:
                    # extract HH:MM:SS.micro
                    try:
                        part = [p for p in l.split() if p.startswith("time=")][0]
                        timestr = part.split("=")[1]
                        # convert to seconds
                        h,m,s = timestr.split(":")
                        s = float(s)
                        secs = int(h)*3600 + int(m)*60 + s
                        est = min(99, int((secs / duration) * 100))
                        jobs[job_id]["progress"] = max(jobs[job_id]["progress"], est)
                    except Exception:
                        pass
                # heartbeat fallback
                if _time.time() - last_update > 1.0:
                    percent = min(98, percent + 2)
                    jobs[job_id]["progress"] = max(jobs[job_id]["progress"], percent)
                    last_update = _time.time()

            rc = proc.poll()
            if rc != 0:
                jobs[job_id].update({"error": f"ffmpeg exit {rc}", "done": False})
            else:
                jobs[job_id].update({"progress": 100, "done": True, "url": f"/renders/{os.path.basename(out_path)}"})

        except Exception as e:
            jobs[job_id].update({"error": str(e), "done": True})
        finally:
            # schedule auto-delete after 10 minutes
            async def cleanup():
                await asyncio.sleep(600)
                jobs.pop(job_id, None)
                try:
                    if os.path.exists(img_path): os.remove(img_path)
                except: pass
                try:
                    if os.path.exists(out_path): os.remove(out_path)
                except: pass
            asyncio.create_task(cleanup())
            queue.task_done()


@app.on_event("startup")
async def start_worker():
    asyncio.create_task(worker())
