## Customer Journey Stitching— OpenVINO + IoU Tracking + ReID

Local demo that:
- Detects **people** (`person-detection-retail-0013`)
- Tracks them (**simple IoU tracker**, no DeepSORT)
- Extracts **ReID embeddings** (`person-reidentification-retail-0287`)
- Assigns **session IDs on ENTRY**
- Closes sessions on **EXIT**
- Visualizes stitched journeys either in an **OpenCV window** (original) or a **browser UI (React)**.

### 1) Setup

**Install Python dependencies:**
```bash
python -m venv .venv
.\.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

**Install Frontend dependencies:**

**PowerShell (Windows):**
```powershell
cd frontend
npm install
cd ..
```

**Or as a one-liner:**
```powershell
cd frontend; npm install; cd ..
```

### 2) Models (MANDATORY)

This repo expects the OpenVINO IR files here:
- `models/person-detection-retail-0013.xml` + `.bin`
- `models/person-reidentification-retail-0287.xml` + `.bin`

### 3) Run

#### 🚀 Quick Start (One Command) - **RECOMMENDED**

Start both backend and frontend with a single command:

**Windows (PowerShell):**
```powershell
python start.py
```

**Windows (Batch file - double-click or run):**
```cmd
start.bat
```

**Windows (PowerShell script):**
```powershell
.\start.ps1
```

This will:
- ✅ Check dependencies
- ✅ Start backend on `http://127.0.0.1:8000`
- ✅ Start frontend on `http://localhost:5173`
- ✅ Open your browser automatically (if configured)

Press `Ctrl+C` to stop both servers.

---

#### Alternative: Manual Start (Two Terminals)

**Terminal 1 (backend):**
```bash
python -m uvicorn backend.server:app --host 127.0.0.1 --port 8000
```

**Terminal 2 (frontend):**
```bash
cd frontend
npm run dev
```

Then open `http://localhost:5173` in your browser.

---

#### OpenCV Window Mode (Original)

For a simple OpenCV window instead of browser UI:

```bash
python main.py --source "https://www.youtube.com/watch?v=VIDEO_ID"
python main.py --source "C:\path\to\video.mp4"
python main.py --source 0  # webcam
```

### 4) Interaction / Controls (Browser UI)

- Enter a source (YouTube URL / local path / webcam index like `0`) and click **Start**
- Click **exactly 2 points** on the video to set the **Entry/Exit line**
- Sessions will populate on the right as ENTRY/EXIT events occur

### 4) Interaction / Controls

- On launch, the **first frame** is shown. Click **exactly 2 points** to define the **Entry/Exit line**.
- Press `q` to quit.

### 5) Session stitching rules

- Cosine similarity threshold: **0.62**
- One session per person per video
- Session is created on **ENTRY**, closed on **EXIT**

### Notes

- This is a demo / prototype for educational clarity (not production hardened).
- Everything runs locally (no cloud, no DB).


