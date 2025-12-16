import os, uuid, asyncio, subprocess, time
from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

os.makedirs("renders", exist_ok=True)

jobs = {}
queue = asyncio.Queue()

app.mount("/", StaticFiles(directory="static", html=True), name="static")
app.mount("/renders", StaticFiles(directory="renders"), name="renders")


@app.post("/render")
async def render(
    image: UploadFile,
    animation: str = Form(...),
    speed: float = Form(...)
):
    job_id = str(uuid.uuid4())
    img_path = f"/tmp/{job_id}.jpg"
    out_path = f"renders/{job_id}.mp4"

    with open(img_path, "wb") as f:
        f.write(await image.read())

    jobs[job_id] = {"progress": 0, "done": False, "url": None}
    await queue.put((job_id, img_path, out_path, animation, speed))

    return {"id": job_id}


@app.get("/progress/{job_id}")
def progress(job_id: str):
    return jobs.get(job_id, {})


async def worker():
    while True:
        job_id, img, out, anim, speed = await queue.get()

        jobs[job_id]["progress"] = 5

        fps = 30
        base_duration = 6
        duration = max(2, int(base_duration / speed))

        anim_map = {
            "lr": f"x='iw*t/{duration}'",
            "rl": f"x='iw-(iw*t/{duration})'",
            "tb": f"y='ih*t/{duration}'",
            "bt": f"y='ih-(ih*t/{duration})'",
            "zoomIn": f"z='1+0.25*t/{duration}'",
            "zoomOut": f"z='1.25-0.25*t/{duration}'",
            "panH": f"x='iw*0.1 + iw*0.8*t/{duration}'",
            "panV": f"y='ih*0.1 + ih*0.8*t/{duration}'",
            "cinematic": f"x='iw*t/{duration}':y='ih-(ih*t/{duration})':z='1+0.15*t/{duration}'"
        }

        vf = f"zoompan={anim_map[anim]}:d={fps*duration}:fps={fps}"

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", img,
            "-vf", vf,
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            out
        ]

        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        jobs[job_id].update({
            "progress": 100,
            "done": True,
            "url": f"/renders/{job_id}.mp4"
        })

        # auto delete após 10 minutos
        await asyncio.sleep(600)
        jobs.pop(job_id, None)
        if os.path.exists(out):
            os.remove(out)

        queue.task_done()


@app.on_event("startup")
async def startup():
    asyncio.create_task(worker())
