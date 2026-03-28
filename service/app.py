# service/app.py

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import time
import tempfile
import asyncio
import uuid
from pathlib import Path

from logging_utils import configure_logging, get_logger, log_params, reset_request_id, set_request_id
from model_handler import generate_audio
from sim_utils import build_index, find_similar, build_slice_index

# ---------- 初始化 FastAPI ----------
app = FastAPI()
configure_logging()
logger = get_logger("api")

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

PRELOAD_DIR = Path("preloaded")                     # 存放预加载 wav 的目录
INDEX_PATH = Path("audio_index.faiss")              # FAISS 索引文件路径
ORDER_FILE = Path("file_order.txt")                 # 对应文件名列表
SLICE_INDEX_PATH = Path("audio_slice_index.faiss")  # 切片 FAISS 索引文件路径
SLICE_MAP_PATH = Path("slice_map.json")             # 切片索引映射文件路径
SLICE_WINDOW_SIZE = 1.0                             # 切片窗口大小（秒）
SLICE_HOP_SIZE = 0.5                                # 切片帧移大小（秒）


async def save_upload(audio_file: UploadFile) -> Path:
    filename = audio_file.filename or f"upload-{int(time.time() * 1000)}.wav"
    tmp_path = UPLOAD_DIR / filename
    data = await audio_file.read()
    tmp_path.write_bytes(data)
    logger.info(
        "[STEP] upload_saved path=%s bytes=%s original_name=%s",
        tmp_path,
        len(data),
        filename,
    )
    return tmp_path


@app.middleware("http")
async def log_request_lifecycle(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    token = set_request_id(request_id)
    start_time = time.perf_counter()
    client = request.client.host if request.client else "-"
    logger.info("[START] method=%s path=%s client=%s", request.method, request.url.path, client)

    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.exception(
            "[ERROR] method=%s path=%s elapsed_ms=%.2f",
            request.method,
            request.url.path,
            elapsed_ms,
        )
        logger.info(
            "[END] method=%s path=%s status=500 elapsed_ms=%.2f",
            request.method,
            request.url.path,
            elapsed_ms,
        )
        reset_request_id(token)
        raise

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    logger.info(
        "[END] method=%s path=%s status=%s elapsed_ms=%.2f",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    reset_request_id(token)
    return response

# ---------- 音乐生成接口 ----------
@app.post("/generate")
async def generate(
    description: str = Form(""),
    audio_file: UploadFile = File(None),
    duration: int = Form(5),
):
    """
    接收文本描述 + 可选音频，调用 MusicGen 生成新音乐并返回 WAV 文件。
    """
    start_time = time.perf_counter()
    log_params(
        logger,
        description_preview=description,
        duration=duration,
        audio_file=audio_file.filename if audio_file else None,
    )

    # 1) 保存上传的音频（若有）
    saved_audio_path = None
    if audio_file:
        saved_audio_path = await save_upload(audio_file)

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
        logger.info(
            "[STEP] generate.completed bytes=%s temp_file=%s handler_ms=%.2f",
            len(audio_bytes),
            tmp.name,
            (time.perf_counter() - start_time) * 1000,
        )

        return FileResponse(
            tmp.name,
            media_type="audio/wav",
            filename="generated.wav"
        )

    except asyncio.CancelledError:
        # 客户端断开
        logger.warning("[ERROR] generate.cancelled_by_client")
        raise HTTPException(status_code=499, detail="Client cancelled")
    except Exception as e:
        logger.exception("[ERROR] generate.failed error=%s", e)
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
    log_params(logger, preload_dir=PRELOAD_DIR, wav_count=len(files))
    if not files:
        logger.warning("[ERROR] rebuild_index.no_wav_files preload_dir=%s", PRELOAD_DIR)
        return JSONResponse({"error": "preloaded 目录下没有 .wav 文件"}, status_code=400)

    # 1) 构建索引并获得文件名列表
    indexed_files = build_index(
        preload_dir=str(PRELOAD_DIR),
        index_path=str(INDEX_PATH)
    )

    # 2) 将文件名顺序写入 ORDER_FILE
    ORDER_FILE.write_text("\n".join(indexed_files), encoding="utf-8")
    logger.info(
        "[STEP] rebuild_index.completed built=%s index_path=%s order_file=%s",
        len(indexed_files),
        INDEX_PATH,
        ORDER_FILE,
    )

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
    log_params(
        logger,
        audio_file=audio_file.filename if audio_file else None,
        top_k=top_k,
    )
    tmp_path = await save_upload(audio_file)

    # 2) 确保索引已存在
    if not INDEX_PATH.exists():
        logger.warning("[ERROR] find_similar.index_missing index_path=%s", INDEX_PATH)
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
        logger.exception("[ERROR] find_similar.failed error=%s", e)
        raise HTTPException(500, "检索失败")
    
    if not matches:
        logger.warning("[ERROR] find_similar.no_matches query_path=%s", tmp_path)
        raise HTTPException(404, "未找到相似音频")

    for rank, (filename, score) in enumerate(zip(matches, scores), start=1):
        logger.info("[RESULT] rank=%s file=%s score=%.4f", rank, filename, score)

    top_file = PRELOAD_DIR / matches[0]
    return FileResponse(str(top_file), media_type="audio/wav", filename=matches[0])

# ---------- 循环录音测试 API ----------
@app.post("/echo")
async def echo(
    audio_file: UploadFile = File(...)
):
    """
    只把上传的 WAV 原样返回。    
    后端直接将 User 这一段音频发回去。
    这里先简单实现“回显”功能以便调试前端循环逻辑。
    """
    log_params(logger, audio_file=audio_file.filename if audio_file else None)
    tmp_path = await save_upload(audio_file)

    # 直接把刚才保存的文件原样返回
    return FileResponse(str(tmp_path), media_type="audio/wav", filename=audio_file.filename)

# ---------- 循环录音 API ----------
@app.post("/loop_audio")
async def loop_audio(
    audio_file: UploadFile = File(...)
):
    """
    当前 loop 流程的正式入口。
    现在仍然回显上传的 WAV，后续可以在这里接生成或检索逻辑。
    """
    log_params(logger, audio_file=audio_file.filename if audio_file else None)
    tmp_path = await save_upload(audio_file)

    # 直接把刚才保存的文件原样返回
    return FileResponse(str(tmp_path), media_type="audio/wav", filename=audio_file.filename)

# 新增：切片索引重建路由
@app.post("/rebuild_slice_index")
async def rebuild_slice_index():
    try:
        log_params(
            logger,
            preload_dir=PRELOAD_DIR,
            window_size=SLICE_WINDOW_SIZE,
            hop_size=SLICE_HOP_SIZE,
        )
        build_slice_index(
            preload_dir=str(PRELOAD_DIR),
            index_path=str(INDEX_PATH),
            map_path=str(SLICE_MAP_PATH),
            window_size=SLICE_WINDOW_SIZE,
            hop_size=SLICE_HOP_SIZE
        )
        logger.info(
            "[STEP] rebuild_slice_index.completed index_path=%s map_path=%s",
            INDEX_PATH,
            SLICE_MAP_PATH,
        )
        return {"detail": "Slice index rebuilt successfully."}
    except Exception as e:
        logger.exception("[ERROR] rebuild_slice_index.failed error=%s", e)
        return JSONResponse({"error": str(e)}, status_code=500)