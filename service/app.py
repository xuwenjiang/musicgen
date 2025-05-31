# service/app.py

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import os
import time
import tempfile
import asyncio
from pathlib import Path

from model_handler import generate_audio
from sim_utils import build_index, find_similar

# ---------- 初始化 FastAPI ----------
app = FastAPI()

# 允许所有源跨域（开发阶段）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 全局目录 & 文件 ----------
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

PRELOAD_DIR = Path("preloaded")            # 存放预加载 wav 的目录
INDEX_PATH = Path("audio_index.faiss")     # FAISS 索引文件路径
ORDER_FILE = Path("file_order.txt")        # 对应文件名列表

# ---------- 音乐生成接口 ----------
@app.post("/generate")
async def generate(
    description: str = Form(...),
    audio_file: UploadFile = File(None),
    duration: int = Form(5),
):
    """
    接收文本描述 + 可选音频，调用 MusicGen 生成新音乐并返回 WAV 文件。
    """
    start_time = time.time()
    print(f"Received /generate: description={description!r}, duration={duration}")

    # 1) 保存上传的音频（若有）
    saved_audio_path = None
    if audio_file:
        saved_audio_path = UPLOAD_DIR / audio_file.filename
        data = await audio_file.read()
        saved_audio_path.write_bytes(data)
        print(f"  • uploaded audio saved to {saved_audio_path}")

    try:
        # 2) 生成音频字节
        audio_bytes = generate_audio(
            description,
            str(saved_audio_path) if saved_audio_path else None,
            duration
        )

        # 3) 写入临时 WAV 文件并返回
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        tmp.write(audio_bytes)
        tmp.close()
        print(f"  • generation time: {time.time() - start_time:.2f}s")

        return FileResponse(
            tmp.name,
            media_type="audio/wav",
            filename="generated.wav"
        )

    except asyncio.CancelledError:
        # 客户端断开
        raise HTTPException(status_code=499, detail="Client cancelled")
    except Exception as e:
        print("Error in /generate:", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------- 索引重建接口 ----------
@app.post("/rebuild_index")
async def rebuild_index():
    """
    遍历 PRELOAD_DIR 下所有 .wav, 计算 embedding 并用 FAISS 建立索引。
    会同时生成 INDEX_PATH 和 ORDER_FILE 两个文件。
    返回 {"built": n}, n 为成功索引的文件数。
    """
    files = sorted(PRELOAD_DIR.glob("*.wav"))
    if not files:
        return JSONResponse({"error": "preloaded 目录下没有 .wav 文件"}, status_code=400)

    # 1) 构建索引并获得文件名列表
    indexed_files = build_index(
        preload_dir=str(PRELOAD_DIR),
        index_path=str(INDEX_PATH)
    )

    # 2) 将文件名顺序写入 ORDER_FILE
    ORDER_FILE.write_text("\n".join(indexed_files), encoding="utf-8")
    print(f"  • indexed {len(indexed_files)} files")

    return {"built": len(indexed_files)}


# ---------- 相似音频检索接口 ----------
@app.post("/find_similar")
async def api_find_similar(
    audio_file: UploadFile = File(...),
    top_k: int = Form(5),
):
    """
    接收上传音频，计算 embedding 并在已建索引中检索最相似的 top_k 条目。
    返回格式：
      {
        "results": [
          {"file": "<filename1>.wav", "score": 0.87},
          ...
        ]
      }
    """
    # 1) 保存上传文件到本地临时目录
    tmp_path = UPLOAD_DIR / audio_file.filename
    with open(tmp_path, "wb") as f:
        f.write(await audio_file.read())

    # 2) 确保索引已存在
    if not INDEX_PATH.exists():
        return JSONResponse({"error": "请先调用 /rebuild_index"}, status_code=400)

    # 3) 调用 sim_utils.find_similar
    try:
        matches, scores = find_similar(
            query_path=str(tmp_path),
            top_k=top_k,
            index_path=str(INDEX_PATH),
            preload_dir=str(PRELOAD_DIR)
        )
        
        # return {"matches": matches, "scores": scores}
    except Exception as e:
        print("find_similar error:", e)
        raise HTTPException(500, "检索失败")
    
    if not matches:
        raise HTTPException(404, "未找到相似音频")
    
    print("---- find_similar results ----")
    for fn, sc in zip(matches, scores):
        print(f"  • {fn}: {sc:.4f}")
    print("------------------------------")

    top_file = PRELOAD_DIR / matches[0]
    return FileResponse(str(top_file), media_type="audio/wav", filename=matches[0])

# ---------- 循环录音专用 API ----------
@app.post("/loop_audio")
async def loop_audio(
    audio_file: UploadFile = File(...)
):
    """
    只把上传的 WAV 原样返回。    
    后端直接将 User 这一段音频发回去。之后你可以改成“/loop_audio”做生成/检索，
    这里先简单实现“回显”功能以便调试前端循环逻辑。
    """
    # 把上传的音频临时存盘
    tmp_path = UPLOAD_DIR / audio_file.filename
    with open(tmp_path, "wb") as f:
        f.write(await audio_file.read())

    # 直接把刚才保存的文件原样返回
    return FileResponse(str(tmp_path), media_type="audio/wav", filename=audio_file.filename)