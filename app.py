import gradio as gr
import os
import math
import subprocess
import logging
from pathlib import Path
import static_ffmpeg

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("videoproc.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

log.info("Starting — pulling FFmpeg binaries...")
static_ffmpeg.add_paths()
log.info("FFmpeg ready.")

APP_DIR    = Path(__file__).parent
INPUT_DIR  = APP_DIR / "input"
OUTPUT_DIR = APP_DIR / "output"
INPUT_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
log.info(f"Input  folder: {INPUT_DIR}")
log.info(f"Output folder: {OUTPUT_DIR}")

EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


def get_ffmpeg():
    import shutil
    return shutil.which("ffmpeg")

def get_ffprobe():
    import shutil
    return shutil.which("ffprobe")


def even(n):
    return int(n) if int(n) % 2 == 0 else int(n) + 1


def calc_output_size(width, height, longest_side):
    if width >= height:
        return longest_side, even((height / width) * longest_side)
    return even((width / height) * longest_side), longest_side


def get_video_info(path):
    path = str(path)
    log.info(f"Probing: {path}")
    ffprobe = get_ffprobe()
    if not ffprobe:
        raise RuntimeError("ffprobe not found.")
    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-of", "csv=p=0", path
    ]
    log.debug(f"cmd: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    log.debug(f"stdout: {r.stdout!r}  stderr: {r.stderr!r}")
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{r.stderr}")
    parts = r.stdout.strip().split("\n")[0].split(",")
    w, h = int(parts[0]), int(parts[1])
    num, den = parts[2].split("/")
    fps = float(num) / float(den)
    dur = float(parts[3]) if len(parts) > 3 else 0
    log.info(f"  -> {w}x{h} @ {fps:.3f}fps  {dur:.2f}s")
    return w, h, fps, dur


def build_scale(width, height, longest_side):
    nw, nh = calc_output_size(width, height, longest_side)
    return f"scale={nw}:{nh}", nw, nh


def run_ffmpeg(cmd):
    log.info(f"ffmpeg: {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error(f"ffmpeg error:\n{r.stderr}")
        raise RuntimeError(r.stderr[-600:])
    log.info("ffmpeg OK.")


def list_input_videos():
    videos = sorted([p.name for p in INPUT_DIR.iterdir() if p.suffix.lower() in EXTENSIONS])
    log.info(f"Input scan: {videos}")
    return videos


# ── TAB 1: Extract Clip — upload + scrubber ───────────────────────────────────

def tab1_load(video_file):
    """Gradio passes the temp path of the uploaded file."""
    if video_file is None:
        return gr.update(), "No video loaded."
    try:
        log.info(f"Tab1 upload received: {video_file}")
        w, h, fps, dur = get_video_info(video_file)
        info = f"{w}x{h} @ {fps:.2f}fps  |  Duration: {dur:.1f}s"
        return gr.update(maximum=round(dur, 1), value=0), info
    except Exception as e:
        log.exception("Tab1 load error")
        return gr.update(), f"Error: {e}"


def tab1_process(video_file, start_time, clip_length, longest_side, target_fps):
    if video_file is None:
        return "No video uploaded."
    try:
        ffmpeg = get_ffmpeg()
        input_path = Path(video_file)
        log.info(f"Tab1 extract from: {input_path}  start={start_time}  length={clip_length}")
        w, h, src_fps, dur = get_video_info(input_path)
        scale, nw, nh = build_scale(w, h, int(longest_side))
        out_fps = min(src_fps, float(target_fps))
        start = max(0.0, float(start_time))
        end = min(start + float(clip_length), dur)
        out_path = OUTPUT_DIR / f"clip_{input_path.stem}_{int(start)}s.mp4"

        run_ffmpeg([
            ffmpeg, "-y",
            "-ss", str(start), "-to", str(end),
            "-i", str(input_path),
            "-vf", f"{scale},fps={out_fps}",
            "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-c:a", "aac", "-b:a", "192k",
            str(out_path)
        ])

        msg = (f"Done.\n"
               f"Input:  {w}x{h} @ {src_fps:.2f}fps\n"
               f"Output: {nw}x{nh} @ {out_fps:.2f}fps\n"
               f"Clip:   {start:.1f}s → {end:.1f}s  ({end-start:.1f}s)\n"
               f"Saved:  {out_path}")
        log.info(msg)
        return msg
    except Exception as e:
        log.exception("Tab1 process error")
        return f"Error: {e}"


# ── TAB 2: Auto Chunk (single video from input folder) ────────────────────────

def tab2_refresh():
    videos = list_input_videos()
    if not videos:
        return gr.update(choices=[], value=None), "No videos found in input\\ folder."
    return gr.update(choices=videos, value=videos[0]), f"{len(videos)} video(s) found."


def tab2_process(video_name, chunk_length, longest_side, target_fps, progress=gr.Progress()):
    if not video_name:
        return "No video selected."
    try:
        ffmpeg = get_ffmpeg()
        ip = INPUT_DIR / video_name
        w, h, src_fps, dur = get_video_info(ip)
        scale, nw, nh = build_scale(w, h, int(longest_side))
        out_fps = min(src_fps, float(target_fps))
        cl = float(chunk_length)
        n = math.ceil(dur / cl)
        log.info(f"Tab2: {n} chunks from {ip.name}")

        for i in progress.tqdm(range(n), desc="Chunking"):
            start = i * cl
            end = min(start + cl, dur)
            out_path = OUTPUT_DIR / f"{ip.stem}_chunk{i+1:03d}.mp4"
            run_ffmpeg([
                ffmpeg, "-y",
                "-ss", str(start), "-to", str(end),
                "-i", str(ip),
                "-vf", f"{scale},fps={out_fps}",
                "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-c:a", "aac", "-b:a", "192k",
                str(out_path)
            ])

        return (f"Done. {n} chunks saved.\n"
                f"Input:  {w}x{h} @ {src_fps:.2f}fps  |  {dur:.1f}s total\n"
                f"Output: {nw}x{nh} @ {out_fps:.2f}fps  |  {cl}s chunks\n"
                f"Saved to: {OUTPUT_DIR.resolve()}")
    except Exception as e:
        log.exception("Tab2 error")
        return f"Error: {e}"


# ── TAB 3: Batch folder chunk ──────────────────────────────────────────────────

def tab3_refresh():
    videos = list_input_videos()
    if not videos:
        return "No videos found in input\\ folder."
    return f"{len(videos)} video(s) ready."


def tab3_process(chunk_length, longest_side, target_fps, progress=gr.Progress()):
    try:
        videos = list_input_videos()
        if not videos:
            return "No videos in input\\ folder."
        ffmpeg = get_ffmpeg()
        cl = float(chunk_length)
        total = 0
        errors = []

        for name in progress.tqdm(videos, desc="Processing"):
            vid = INPUT_DIR / name
            try:
                w, h, src_fps, dur = get_video_info(vid)
                scale, nw, nh = build_scale(w, h, int(longest_side))
                out_fps = min(src_fps, float(target_fps))
                n = math.ceil(dur / cl)
                for i in range(n):
                    start = i * cl
                    end = min(start + cl, dur)
                    out_path = OUTPUT_DIR / f"{vid.stem}_chunk{i+1:03d}.mp4"
                    run_ffmpeg([
                        ffmpeg, "-y",
                        "-ss", str(start), "-to", str(end),
                        "-i", str(vid),
                        "-vf", f"{scale},fps={out_fps}",
                        "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-c:a", "aac", "-b:a", "192k",
                        str(out_path)
                    ])
                    total += 1
            except Exception as e:
                log.exception(f"Error on {name}")
                errors.append(f"{name}: {e}")

        summary = (f"Done. {len(videos)} videos → {total} chunks saved.\n"
                   f"Output: {OUTPUT_DIR.resolve()}")
        if errors:
            summary += "\n\nErrors:\n" + "\n".join(errors)
        return summary
    except Exception as e:
        log.exception("Tab3 error")
        return f"Error: {e}"


# ── UI ────────────────────────────────────────────────────────────────────────

RES_CHOICES = ["512", "768"]
FPS_CHOICES = ["25", "24", "30", "23.976"]

with gr.Blocks(title="Dragon Diffusion — Video Prep") as app:

    gr.Markdown("# 🐉 Dragon Diffusion — Video Prep Tool")
    gr.Markdown(
        "Prepares source footage for LoRA training datasets. "
        "Output: H.264 MP4, resized to longest side, audio preserved, codec-friendly. "
        "All output saves to **`output\\`** next to this app. Log: `videoproc.log`."
    )

    # ── Tab 1 ──
    with gr.Tab("✂️ Extract Clip"):
        gr.Markdown(
            "Upload a video and scrub to your start point using the player. "
            "Hit **📍 Set Start Here** to capture the current position — the Start slider will update automatically. "
            "Fine-tune with the slider if needed, set clip length, then hit **Extract Clip**."
        )
        with gr.Row():
            with gr.Column(scale=1):
                t1_video  = gr.Video(label="Source Video — scrub to your in-point, then hit Set Start Here",
                                     sources=["upload"], interactive=True,
                                     include_audio=True)
                t1_info   = gr.Textbox(label="Video Info", interactive=False, lines=1)
                with gr.Row():
                    t1_set_start = gr.Button("📍 Set Start Here", variant="secondary", scale=2)
                    t1_clear     = gr.Button("↩ Reset Start", variant="secondary", scale=1)
                t1_start  = gr.Slider(0, 100, value=0, step=0.1,
                                      label="Start Position (seconds) — captured from scrubber or drag to fine-tune")
                t1_length = gr.Slider(1, 10, value=5, step=0.5,
                                      label="Clip Length (seconds)")
                with gr.Row():
                    t1_res = gr.Radio(RES_CHOICES, value="768", label="Longest Side (px)")
                    t1_fps = gr.Dropdown(FPS_CHOICES, value="25", label="Target FPS")
                t1_btn = gr.Button("Extract Clip", variant="primary")
            with gr.Column(scale=1):
                t1_out = gr.Textbox(label="Result", interactive=False, lines=8)

        t1_video.change(tab1_load, inputs=t1_video, outputs=[t1_start, t1_info])

        # Read currentTime from the video player via JS and push into the slider
        t1_set_start.click(
            fn=None,
            inputs=[],
            outputs=[t1_start],
            js="""
            () => {
                // Gradio renders <video> inside the component block; grab the first one in Tab 1
                const videos = document.querySelectorAll('video');
                let currentTime = 0;
                for (const v of videos) {
                    if (v.duration && v.duration > 0) {
                        currentTime = v.currentTime;
                        break;
                    }
                }
                return currentTime;
            }
            """
        )

        t1_clear.click(fn=lambda: 0.0, inputs=[], outputs=[t1_start])

        t1_btn.click(tab1_process,
                     inputs=[t1_video, t1_start, t1_length, t1_res, t1_fps],
                     outputs=t1_out)

    # ── Tab 2 ──
    with gr.Tab("🔪 Auto Chunk — Single Video"):
        gr.Markdown(
            "Drop video into **`input\\`**, hit **Refresh**, pick it, set chunk length, go."
        )
        with gr.Row():
            with gr.Column():
                t2_status  = gr.Textbox(label="Input Folder", interactive=False, lines=1,
                                        value="Hit Refresh to scan input\\ folder.")
                t2_picker  = gr.Dropdown(label="Select Video", choices=[], interactive=True)
                t2_refresh = gr.Button("🔄 Refresh", size="sm")
                t2_chunk   = gr.Slider(10, 300, value=90, step=5,
                                       label="Chunk Length (seconds)")
                with gr.Row():
                    t2_res = gr.Radio(RES_CHOICES, value="768", label="Longest Side (px)")
                    t2_fps = gr.Dropdown(FPS_CHOICES, value="25", label="Target FPS")
                t2_btn = gr.Button("Chunk Video", variant="primary")
            with gr.Column():
                t2_out = gr.Textbox(label="Result", interactive=False, lines=10)

        t2_refresh.click(tab2_refresh, outputs=[t2_picker, t2_status])
        t2_btn.click(tab2_process,
                     inputs=[t2_picker, t2_chunk, t2_res, t2_fps],
                     outputs=t2_out)

    # ── Tab 3 ──
    with gr.Tab("📁 Batch — Entire Input Folder"):
        gr.Markdown(
            "Drop all source videos into **`input\\`**, hit **Refresh** to confirm count, "
            "then **Process All**."
        )
        with gr.Row():
            with gr.Column():
                t3_status  = gr.Textbox(label="Input Folder", interactive=False, lines=1,
                                        value="Hit Refresh to scan input\\ folder.")
                t3_refresh = gr.Button("🔄 Refresh", size="sm")
                t3_chunk   = gr.Slider(10, 300, value=90, step=5,
                                       label="Chunk Length (seconds)")
                with gr.Row():
                    t3_res = gr.Radio(RES_CHOICES, value="768", label="Longest Side (px)")
                    t3_fps = gr.Dropdown(FPS_CHOICES, value="25", label="Target FPS")
                t3_btn = gr.Button("Process All", variant="primary")
            with gr.Column():
                t3_out = gr.Textbox(label="Result", interactive=False, lines=10)

        t3_refresh.click(tab3_refresh, outputs=t3_status)
        t3_btn.click(tab3_process,
                     inputs=[t3_chunk, t3_res, t3_fps],
                     outputs=t3_out)

if __name__ == "__main__":
    app.launch(inbrowser=True,
               allowed_paths=[str(INPUT_DIR), str(OUTPUT_DIR)])
