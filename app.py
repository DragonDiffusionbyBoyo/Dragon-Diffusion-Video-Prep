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

    # Stream-level query for w, h, fps, and duration
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

    # Try stream duration first; fall back to format duration (handles webm/mkv)
    dur = None
    if len(parts) > 3 and parts[3].strip() not in ("N/A", ""):
        try:
            dur = float(parts[3])
        except ValueError:
            pass

    if dur is None:
        log.info("  Stream duration N/A — querying format duration.")
        cmd2 = [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", path
        ]
        r2 = subprocess.run(cmd2, capture_output=True, text=True)
        log.debug(f"format dur stdout: {r2.stdout!r}")
        try:
            dur = float(r2.stdout.strip().split("\n")[0])
        except (ValueError, IndexError):
            dur = 0.0
            log.warning("  Could not determine duration; defaulting to 0.")

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

# ── Viggle constants ───────────────────────────────────────────────────────────
VIGGLE_FPS        = 24
VIGGLE_RES_CHOICES = ["832×480 (16:9)", "1280×736 (16:9)"]
VIGGLE_RES_MAP     = {
    "832×480 (16:9)":  (832, 480),
    "1280×736 (16:9)": (1280, 736),
}
# Valid Viggle output frame counts: 17n+5, n=5..20 → 90,107,124,...345
VIGGLE_VALID_FRAMES = [17 * n + 5 for n in range(5, 21)]   # 90 → 345


def viggle_nearest_frames(duration_secs, fps=VIGGLE_FPS):
    """Return (below, above) valid Viggle frame counts bracketing this duration."""
    total = duration_secs * fps
    below = max((f for f in VIGGLE_VALID_FRAMES if f <= total), default=VIGGLE_VALID_FRAMES[0])
    above = min((f for f in VIGGLE_VALID_FRAMES if f >= total), default=VIGGLE_VALID_FRAMES[-1])
    return below, above


def viggle_frame_info(duration_secs):
    """Human-readable string about valid frame counts for this clip."""
    total = round(duration_secs * VIGGLE_FPS)
    below, above = viggle_nearest_frames(duration_secs)
    lines = [
        f"Clip at {VIGGLE_FPS}fps = {total} frames  ({duration_secs:.2f}s)",
        f"Nearest valid Viggle counts:  ↓ {below} frames ({below/VIGGLE_FPS:.2f}s)  |  ↑ {above} frames ({above/VIGGLE_FPS:.2f}s)",
        "",
        "All valid counts (17n+5, 90–345):",
        "  " + "  ".join(f"{f}({f/VIGGLE_FPS:.1f}s)" for f in VIGGLE_VALID_FRAMES),
    ]
    return "\n".join(lines)


def detect_scenes(video_path, threshold=0.3):
    """
    Use ffmpeg scene filter to find scene-change timestamps.
    Returns list of (timestamp_secs, frame_index) tuples.
    threshold: 0.0–1.0, lower = more sensitive.
    """
    ffmpeg = get_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found.")
    # On Windows, single-quotes inside -vf break cmd.exe — use double-quotes
    # subprocess with a list bypasses the shell entirely so we just need the
    # expression without any shell quoting at all.
    cmd = [
        ffmpeg, "-i", str(video_path),
        "-vf", f"select=gt(scene\\,{threshold}),showinfo",
        "-vsync", "vfr",
        "-f", "null", "-"
    ]
    log.info(f"Scene detect: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    # showinfo writes to stderr
    scenes = []
    for line in r.stderr.splitlines():
        if "showinfo" in line and "pts_time" in line:
            try:
                pts_part = [p for p in line.split() if "pts_time" in p][0]
                t = float(pts_part.split(":")[1])
                n_part = [p for p in line.split() if p.startswith("n:")]
                frame = int(n_part[0].split(":")[1]) if n_part else 0
                scenes.append((round(t, 3), frame))
            except Exception:
                continue
    log.info(f"Found {len(scenes)} scene changes.")
    return scenes


def viggle_prep_load(video_file):
    """Load video and return info + scene analysis."""
    if video_file is None:
        return gr.update(), "No video loaded.", ""
    try:
        w, h, fps, dur = get_video_info(video_file)
        info = f"{w}x{h} @ {fps:.2f}fps  |  {dur:.1f}s  |  {round(dur*fps)} frames"
        frame_info = viggle_frame_info(dur)
        return gr.update(maximum=round(dur, 1), value=0), info, frame_info
    except Exception as e:
        log.exception("Viggle load error")
        return gr.update(), f"Error: {e}", ""


def viggle_detect_scenes(video_file, threshold):
    """Run scene detection and return formatted results."""
    if video_file is None:
        return "No video loaded."
    try:
        scenes = detect_scenes(video_file, float(threshold))
        if not scenes:
            return "No scene changes detected at this threshold. Try lowering it."
        lines = [f"Found {len(scenes)} scene change(s):\n"]
        for i, (t, f) in enumerate(scenes, 1):
            frame_24 = round(t * VIGGLE_FPS)
            lines.append(f"  Scene {i:>3}:  {t:>8.3f}s  →  frame {frame_24:>5} @ {VIGGLE_FPS}fps")
        lines.append(f"\nUse these frame indices as 'pins' in the Viggle Remake Conditioning node.")
        lines.append(f"Remember: max 4 pins (ref images) per Viggle generation.")
        return "\n".join(lines)
    except Exception as e:
        log.exception("Scene detect error")
        return f"Error: {e}"


def viggle_extract_refs(video_file, ref_times_str):
    """
    Extract reference frames as PNGs at specified timestamps.
    ref_times_str: comma-separated seconds, e.g. "0.5, 3.2, 7.8"
    Returns status message.
    """
    if video_file is None:
        return "No video loaded."
    try:
        ffmpeg  = get_ffmpeg()
        ip      = Path(video_file)
        times   = [float(t.strip()) for t in ref_times_str.split(",") if t.strip()]
        if not times:
            return "No timestamps entered."
        if len(times) > 4:
            return "Viggle supports max 4 reference images. Enter up to 4 timestamps."

        out_paths = []
        for i, t in enumerate(times, 1):
            frame_idx = round(t * VIGGLE_FPS)
            out_path  = OUTPUT_DIR / f"viggle_ref{i}_t{t:.3f}s_f{frame_idx}.png"
            run_ffmpeg([
                ffmpeg, "-y",
                "-ss", str(t),
                "-i", str(ip),
                "-frames:v", "1",
                "-q:v", "2",
                str(out_path)
            ])
            out_paths.append((i, t, frame_idx, out_path))

        lines = [f"Extracted {len(out_paths)} reference frame(s) to output\\ folder:\n"]
        lines.append("  Ref  |  Time (s)  |  Frame @ 24fps  |  Filename")
        lines.append("  " + "-"*60)
        for i, t, f, p in out_paths:
            lines.append(f"  ref{i}  |  {t:>8.3f}s  |  frame {f:>5}       |  {p.name}")
        lines.append(f"\nPin values for ComfyUI: {', '.join(str(f) for _, _, f, _ in out_paths)}")
        return "\n".join(lines)
    except Exception as e:
        log.exception("Ref extract error")
        return f"Error: {e}"


def viggle_process(video_file, resolution, use_frames, start_time, progress=gr.Progress()):
    """
    Convert a clip to Viggle-ready format:
    - 24fps exactly
    - 832x480 or 1280x736 with centre-crop to maintain aspect
    - trim to nearest valid Viggle frame count
    """
    if video_file is None:
        return "No video loaded."
    try:
        ffmpeg = get_ffmpeg()
        ip     = Path(video_file)
        w, h, src_fps, dur = get_video_info(ip)
        tw, th = VIGGLE_RES_MAP[resolution]

        # Pick target frame count
        target_frames = int(use_frames)
        dur_trimmed   = target_frames / VIGGLE_FPS

        start = float(start_time)
        end   = start + dur_trimmed

        # Scale + centre-crop to exact Viggle resolution
        # scale to fill then crop centre
        vf = (
            f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th}"
            f",fps={VIGGLE_FPS}"
        )

        out_path = OUTPUT_DIR / f"viggle_{ip.stem}_{tw}x{th}_{target_frames}f.mp4"
        run_ffmpeg([
            ffmpeg, "-y",
            "-ss", str(start), "-to", str(end),
            "-i", str(ip),
            "-vf", vf,
            "-c:v", "libx264", "-crf", "18", "-preset", "slow",
            "-c:a", "aac", "-b:a", "192k",
            "-r", str(VIGGLE_FPS),
            str(out_path)
        ])

        msg = (
            f"Done — Viggle-ready clip saved.\n\n"
            f"Input:   {w}x{h} @ {src_fps:.2f}fps\n"
            f"Output:  {tw}x{th} @ {VIGGLE_FPS}fps\n"
            f"Frames:  {target_frames}  ({dur_trimmed:.2f}s)\n"
            f"Saved:   {out_path}\n\n"
            f"Remember to convert source to 24fps before feeding to Viggle Remake!"
        )
        return msg
    except Exception as e:
        log.exception("Viggle process error")
        return f"Error: {e}"

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

    # ── Tab 4: Viggle Remake Prep ──────────────────────────────────────────────
    with gr.Tab("🎬 Viggle Remake Prep"):
        gr.Markdown(
            "Prepares clips for **Viggle Remake** (MiniMax H3). "
            "Forces 24fps, crops to exact 832×480 or 1280×736, trims to a valid Viggle frame count (17n+5, 90–345). "
            "Also detects scene changes and extracts reference PNGs with their frame indices for use as pins in ComfyUI."
        )
        with gr.Row():
            # ── Left column: inputs ───────────────────────────────────────────
            with gr.Column(scale=1):
                v4_video   = gr.Video(label="Source Video", sources=["upload"],
                                      interactive=True, include_audio=True)
                v4_info    = gr.Textbox(label="Video Info", interactive=False, lines=1)
                v4_frames  = gr.Textbox(label="Valid Viggle Frame Counts", interactive=False, lines=5)

                gr.Markdown("### 1 — Scene Detection")
                v4_thresh  = gr.Slider(0.1, 0.6, value=0.3, step=0.05,
                                       label="Scene Change Threshold (lower = more sensitive)")
                v4_detect  = gr.Button("🔍 Detect Scene Changes", variant="secondary")
                v4_scenes  = gr.Textbox(label="Scene Changes (timestamps + frame indices @ 24fps)",
                                        interactive=False, lines=10)

                gr.Markdown("### 2 — Extract Reference Frames")
                gr.Markdown(
                    "Enter up to 4 timestamps (seconds, comma-separated) — "
                    "use the scene change list above to pick good frames. "
                    "These become your `ref_1`…`ref_4` images and the frame indices are your **pins**."
                )
                v4_reftimes = gr.Textbox(label="Reference Timestamps (s) — e.g.  0.5, 3.2, 7.8, 12.0",
                                         placeholder="0.5, 3.2, 7.8", lines=1)
                v4_extbtn   = gr.Button("📸 Extract Reference Frames", variant="secondary")
                v4_refout   = gr.Textbox(label="Extracted Refs + Pin Values", interactive=False, lines=8)

                gr.Markdown("### 3 — Export Viggle-Ready Clip")
                v4_res      = gr.Radio(VIGGLE_RES_CHOICES, value=VIGGLE_RES_CHOICES[0],
                                       label="Output Resolution")
                v4_start    = gr.Slider(0, 300, value=0, step=0.1,
                                        label="Start Position (seconds)")
                v4_useframes = gr.Dropdown(
                    choices=[str(f) for f in VIGGLE_VALID_FRAMES],
                    value=str(VIGGLE_VALID_FRAMES[4]),   # 141 frames ~ 5.9s
                    label="Target Frame Count (17n+5) — pick the count that fits your scene"
                )
                v4_export   = gr.Button("⚙️ Export Viggle Clip", variant="primary")

            # ── Right column: outputs ─────────────────────────────────────────
            with gr.Column(scale=1):
                v4_exportout = gr.Textbox(label="Export Result", interactive=False, lines=10)
                gr.Markdown(
                    "#### Quick Reference\n"
                    "| Frames | Duration |\n"
                    "|--------|----------|\n"
                    + "\n".join(
                        f"| {f} | {f/24:.2f}s |"
                        for f in VIGGLE_VALID_FRAMES
                    )
                    + "\n\n"
                    "**Pin values** = frame index @ 24fps of each ref image.\n\n"
                    "**Max 4 refs** per Viggle generation.\n\n"
                    "Source must be **24fps** before feeding to Viggle Remake node — this tab handles that conversion.\n\n"
                    "Output files land in `output\\` next to this app."
                )

        v4_video.change(viggle_prep_load, inputs=v4_video,
                        outputs=[v4_start, v4_info, v4_frames])
        v4_detect.click(viggle_detect_scenes, inputs=[v4_video, v4_thresh],
                        outputs=v4_scenes)
        v4_extbtn.click(viggle_extract_refs, inputs=[v4_video, v4_reftimes],
                        outputs=v4_refout)
        v4_export.click(viggle_process,
                        inputs=[v4_video, v4_res, v4_useframes, v4_start],
                        outputs=v4_exportout)

if __name__ == "__main__":
    app.launch(inbrowser=True,
               allowed_paths=[str(INPUT_DIR), str(OUTPUT_DIR)])
