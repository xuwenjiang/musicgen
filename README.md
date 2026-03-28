# Local Interactive MusicGen Prototype

This repository contains everything you need to run a local interactive music-generation experiment using Meta's MusicGen model. The project is organized into three main folders:

- **audiocraft**: The cloned AudioCraft repository (MusicGen code).
- **service**: A FastAPI backend that exposes `/generate` endpoint.
- **frontend**: A simple web app for recording audio, entering text prompts, and playing back generated music.

---

## Prerequisites

- **Operating System**: Windows 10/11 (tested).
- **CUDA**: NVIDIA GPU drivers + CUDA 11.8 or 12.1 installed (optional for GPU acceleration).
- **Conda**: Anaconda or Miniconda installed.

## 1. Create and activate Conda environment

Open PowerShell or Anaconda Prompt and run:

```powershell
conda create -n musicgen python=3.9 -y
conda activate musicgen
```

## 2. Install PyTorch

If conda dependency solving is slow on your machine, install `mamba` first:

```powershell
conda install -n base -c conda-forge mamba -y
```

Then install the core packages for your setup (`mamba` recommended):

- **GPU (CUDA 11.8)**

  ```powershell
  mamba install numpy=1.26 pytorch==2.1.0 torchvision==0.16.0 torchaudio==2.1.0 pytorch-cuda=11.8 -c pytorch -c nvidia -y
  ```

  If you prefer conda:

  ```powershell
  conda install numpy=1.26 pytorch==2.1.0 torchvision==0.16.0 torchaudio==2.1.0 pytorch-cuda=11.8 -c pytorch -c nvidia -y
  ```

- **CPU only**

  ```powershell
  mamba install numpy=1.26 pytorch==2.1.0 torchvision==0.16.0 torchaudio==2.1.0 cpuonly -c pytorch -y
  ```

  If you prefer conda:

  ```powershell
  conda install numpy=1.26 pytorch==2.1.0 torchvision==0.16.0 torchaudio==2.1.0 cpuonly -c pytorch -y
  ```

Quick verification:

```powershell
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

## 3. Install AudioCraft (MusicGen)

From the project root, install the Windows prerequisites first. AudioCraft needs both `ffmpeg` at runtime and `av==11.0.0` for Python-side audio I/O:

```powershell
mamba install -c conda-forge ffmpeg "av=11.0.0" -y
```

Then install AudioCraft:

```powershell
cd audiocraft
pip install --upgrade pip setuptools wheel
pip install -e .

# 🔒 Pin transformers to a known compatible version (newer versions may break AudioCraft)
pip install transformers==4.38.2
```

Verify installation:

```powershell
python -c "import torch, transformers; print(torch.__version__); print(transformers.__version__)"
```

## 4. Install backend dependencies

From the project root:

```powershell
cd service
# FastAPI + core audio libraries
pip install fastapi uvicorn python-multipart aiofiles librosa pydub soundfile

# ⚠️ IMPORTANT (Windows):
# Do NOT install faiss-cpu from conda-forge.
# It may downgrade MKL/TBB and break PyTorch.
# Always use the PyPI version:
pip install faiss-cpu

# Verify that both torch and faiss still import correctly
python -c "import torch, transformers, faiss; print(torch.__version__); print(transformers.__version__); print(faiss.__version__)"

# Check whether a Hugging Face token is already configured
hf auth whoami
# If you are not logged in yet, sign in with your token
hf auth login
```

## 5. Install frontend dependencies

No install needed—frontend is pure HTML/JS. Ensure you have downloaded `Recorder.js` or other libs into `frontend/libs/`.

## Directory Structure

```
MusicGen/
├── audiocraft/       # MusicGen model code (AudioCraft)
├── service/          # FastAPI backend
|   ├── preloaded/    # Sample WAV files for testing
│   ├── scripts/      # Scripts
│   ├── app.py
│   ├── model_handler.py
│   ├── audio_utils.py
│   ├── sim_utils.py  # CLAP + FAISS search
│   └── requirements.txt
└── frontend/         # Web UI
    ├── index.html
    ├── style.css
    ├── app.js
    └── libs/         # JS libraries (e.g. recorder.js)
```

---

# Running the Service + Frontend

## 1. Start the backend service

In a `musicgen` shell:

```powershell
cd service
uvicorn app:app --host 0.0.0.0 --port 8000
```

## 2. Open the frontend

In a `musicgen` shell:

```powershell
cd frontend
http-server -p 8080
```

# Usage Example

- **Record**: Click "Start Recording", then "Stop Recording".
- **Prompt**: Type a text description (e.g. `"rock cello"`).
- **Generate**: Click "Generate Music" to hear the AI-generated audio.

## cURL example (PowerShell)

```powershell
curl.exe -Method POST "http://localhost:8000/generate" `
  -Form description="A calm piano melody" `
  -Form duration=5 `
  --output demo_output.wav
```

> You can then play `demo_output.wav` locally.

---

Feel free to customize prompts, extend the service, or integrate into your own application. Happy experimenting!

