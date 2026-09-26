# 🐉 Dragon Diffusion — Video Prep Tool

A simple tool to prepare videos for AI LoRA training. It resizes your videos, fixes the frame rate, and cuts them into the right length clips — all ready to go straight into the captioning and training workflow.

---

## Before You Start

You need **Python 3.10 or later** installed on your computer.

Download it from [https://www.python.org/downloads/](https://www.python.org/downloads/)

During installation, tick the box that says **"Add Python to PATH"** — this is important.

---

## First Time Setup

1. Put all the files (`app.py`, `install.bat`, `run.bat`) into a folder on your computer — anywhere you like, e.g. `C:\VideoPrep\`
2. Double-click **`install.bat`**
3. Wait for it to finish — it will download everything it needs including FFmpeg. This only needs to be done once.

---

## Every Time After That

Double-click **`run.bat`** — the app will open in your browser automatically.

---

## The Three Tabs

### ✂️ Tab 1 — Extract Clip
**Use this when you want to pull a specific moment from a longer video.**

1. Click **Upload Video** and choose your file
2. Use the video scrubber to find the moment you want to start from
3. Set the **Start Position** slider to match where you want to begin
4. Set **Clip Length** — 5 or 6 seconds is recommended
5. Choose your output size — **768** is recommended
6. Leave FPS at **25** unless you have a reason to change it
7. Click **Extract Clip**
8. Your clip saves to the **`output\`** folder

---

### 🔪 Tab 2 — Auto Chunk (Single Video)
**Use this when you have one longer video and want to split the whole thing into training clips.**

1. Copy your video into the **`input\`** folder next to the app
2. Click **Refresh** — your video should appear in the dropdown
3. Select it
4. Set **Chunk Length** — 90 seconds is the default, good for most source footage
5. Choose output size and FPS
6. Click **Chunk Video**
7. All chunks save to the **`output\`** folder

---

### 📁 Tab 3 — Batch (Entire Folder)
**Use this when you have a whole folder of videos to process in one go.**

1. Copy all your videos into the **`input\`** folder next to the app
2. Click **Refresh** — it will tell you how many videos it found
3. Set **Chunk Length**, output size, and FPS
4. Click **Process All**
5. Everything saves to the **`output\`** folder

---

## Important — Video Size

**Do not feed in 4K or HD footage directly.** Your computer will run out of memory.

Before using this tool, make sure your source videos are a sensible size. If you have 4K footage, use a video editor to scale it down first.

The tool outputs at **768px** on the longest side by default — this is the correct size for training.

---

## Output

All processed videos go into the **`output\`** folder next to the app.

They are saved as:
- H.264 MP4
- Resized to your chosen longest side (512 or 768px)
- Original aspect ratio preserved — nothing is cropped
- Audio preserved
- Frame rate set to your chosen FPS (default 25)

These files are ready to go straight into the Dragon Diffusion captioning workflow.

---

## Something Went Wrong?

Check the **`videoproc.log`** file in the same folder as the app. It records everything the tool did and will show you exactly what went wrong.

---

## Folder Layout

```
Your folder/
├── app.py
├── install.bat
├── run.bat
├── README.md
├── videoproc.log       ← created when you run the app
├── input\              ← put your source videos here (Tabs 2 and 3)
└── output\             ← your processed clips appear here
```
