# service/sim_utils.py

import faiss
import numpy as np
import soundfile as sf
import librosa
import json
from transformers import ClapProcessor, ClapModel
from pathlib import Path
import torch

from logging_utils import get_logger


logger = get_logger("sim")

# ---------------------------------------------------------------------
# 1. 初始化 CLAP Processor & Model（首次会从 HF 下载，之后可离线启动）
# ---------------------------------------------------------------------
processor = ClapProcessor.from_pretrained("laion/clap-htsat-unfused")
model = (
    ClapModel
    .from_pretrained("laion/clap-htsat-unfused")
    .eval()  # 推理模式
    .to("cuda" if torch.cuda.is_available() else "cpu")
)

# CLAP 期望的采样率（通常为 48 kHz）
TARGET_SR = processor.feature_extractor.sampling_rate


def _embed_wave(wav: np.ndarray, sr: int) -> np.ndarray:
    """
    将 numpy waveform 转成 CLAP embedding 向量 (float32 1D array)。

    步骤：
    1) 如果输入采样率 != TARGET_SR，先重采样
    2) 用 processor 构造输入 tensors（注意 audios 参数要是列表）
    3) 移到 model.device，然后 no_grad 推理
    4) 返回 feats[0]（batch size=1）的 numpy 向量
    """
    # 1) 重采样
    if sr != TARGET_SR:
        wav = librosa.resample(wav, orig_sr=sr, target_sr=TARGET_SR)
        sr = TARGET_SR

    # 2) 构造输入，注意 audios 要用列表包裹
    inputs = processor(
        audios=[wav],
        sampling_rate=sr,
        return_tensors="pt",
        padding=True,
    )
    # 3) 移到同一设备
    inputs = {k: v.to(model.device) for k, v in inputs.items()}

    # 4) 推理并取第一个 embedding
    with torch.no_grad():
        feats = model.get_audio_features(**inputs)  # (batch, dim)
    emb = feats[0].cpu().numpy().astype("float32")
    return emb


def build_index(preload_dir: str, index_path: str = "audio_index.faiss") -> list[str]:
    """
    遍历 preload_dir 下所有 .wav：
      • 读取音频 → 调用 _embed_wave 得到 embedding
      • 堆叠成 (N, D) 数组 xb → 构建 FAISS 索引 (内积搜索)
      • 保存 index 到 index_path

    返回：已索引的文件名列表
    """
    files = sorted(Path(preload_dir).glob("*.wav"))
    logger.info("[STEP] build_index.start preload_dir=%s files=%s", preload_dir, len(files))
    embeddings = []

    for p in files:
        wav, sr = sf.read(str(p))
        emb = _embed_wave(wav, sr)
        embeddings.append(emb)

    xb = np.stack(embeddings, axis=0)           # (N, D)
    index = faiss.IndexFlatIP(xb.shape[1])      # 用内积度量相似度
    index.add(xb)
    faiss.write_index(index, index_path)
    logger.info(
        "[STEP] build_index.end index_path=%s items=%s embedding_dim=%s",
        index_path,
        xb.shape[0],
        xb.shape[1],
    )

    return [p.name for p in files]

def build_slice_index(
    preload_dir: str,
    index_path: str = "audio_slice_index.faiss",
    map_path: str = "slice_map.json",
    window_size: float = 1.0,
    hop_size: float = 1.0
) -> None:
    """
    对 preload_dir 下所有 .wav 做“1s 切片 + CLAP embed + FAISS 内积索引”，
    并把索引保存到 index_path，把切片映射 (slice_map) 保存到 map_path (JSON)。

    切片策略：以 window_size 秒为一段，帧移 hop_size 秒。例如 window=1s, hop=1s。
    """
    preload_path = Path(preload_dir)
    wav_files = sorted(preload_path.glob("*.wav"))
    logger.info(
        "[STEP] build_slice_index.start preload_dir=%s files=%s window_size=%s hop_size=%s",
        preload_dir,
        len(wav_files),
        window_size,
        hop_size,
    )
    if not wav_files:
        raise ValueError(f"目录 {preload_dir} 下没有找到任何 .wav 文件。")

    all_embeddings = []
    slice_map = []  # 形如 [{"filename": ..., "start_time": ...}, ...]

    for wav_path in wav_files:
        # 加载整个 wav
        wav, sr = sf.read(str(wav_path))  # wav 形状 (T,) 或 (T, C)
        if wav.ndim > 1:
            wav = wav[:, 0]  # 取第一声道

        duration = len(wav) / sr  # 秒数
        num_windows = int(np.floor((duration - window_size) / hop_size)) + 1
        # 对每个窗口切片
        for i in range(num_windows):
            start_time_sec = i * hop_size
            start_sample = int(start_time_sec * sr)
            end_sample = start_sample + int(window_size * sr)

            # 注意：最后一段可能需要截断或填零
            if end_sample > len(wav):
                clip = wav[start_sample:].copy()
                pad_len = end_sample - len(wav)
                clip = np.concatenate([clip, np.zeros(pad_len, dtype=wav.dtype)])
            else:
                clip = wav[start_sample:end_sample]

            # 这里用你原来的 _embed_wave
            emb = _embed_wave(clip, sr)
            all_embeddings.append(emb)
            slice_map.append({
                "filename": wav_path.name,
                "start_time": round(start_time_sec, 3)
            })
            
    # 整理成 numpy 数组
    xb = np.stack(all_embeddings, axis=0).astype("float32")  # (M, D)
    D = xb.shape[1]

    # 建立 FAISS 索引
    index = faiss.IndexFlatIP(D)
    index.add(xb)
    faiss.write_index(index, index_path)
    logger.info("[STEP] build_slice_index.index_saved index_path=%s slices=%s dim=%s", index_path, xb.shape[0], D)

    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(slice_map, f, ensure_ascii=False, indent=2)
    logger.info("[STEP] build_slice_index.map_saved map_path=%s records=%s", map_path, len(slice_map))

def find_similar(
    query_path: str,
    top_k: int = 5,
    index_path: str = "audio_index.faiss",
    preload_dir: str = "preloaded"
) -> tuple[list[str], list[float]]:
    """
    对 query_path:
      1) 读取 wav → _embed_wave → 得到 (D,) 向量 qemb
      2) 载入 FAISS 索引 → search(qemb[None], top_k)
      3) 根据索引结果 I 映射回 preload_dir 中的文件名
      4) 返回 (matches, scores)

    matches: 最相似文件名列表
    scores:  对应的内积分数列表
    """
    wav, sr = sf.read(query_path)
    logger.info("[STEP] find_similar.start query_path=%s top_k=%s index_path=%s", query_path, top_k, index_path)
    qemb = _embed_wave(wav, sr)[None]  # (1, D)

    index = faiss.read_index(index_path)
    D, I = index.search(qemb, top_k)  # D: (1, top_k), I: (1, top_k)

    files = sorted(Path(preload_dir).glob("*.wav"))
    matches = [files[i].name for i in I[0]]
    scores = D[0].tolist()
    logger.info("[STEP] find_similar.end matches=%s", len(matches))

    return matches, scores

def query_slices_from_index(
    query_wav_path: str,
    index_path: str = "audio_slice_index.faiss",
    map_path: str = "slice_map.json",
    window_size: float = 1.0,
    hop_size: float = 1.0,
    top_k: int = 1
):
    """
    给一个 query_wav(长度 ≤ 5 秒)，按 1s 切片(hop=1s) → embed → FAISS 检索 → 输出每段最相似预置切片的信息。

    top_k: 每段想要返回多少个相似结果(这里我们常用 k=1)。
    返回: 一个 list, 每个元素格式为 [(filename, start_time, similarity_score), ...]
    """

    # 1. 加载 slice_map
    with open(map_path, "r", encoding="utf-8") as f:
        slice_map = json.load(f)  # list of {"filename": "...", "start_time": ...}

    # 2. 读取 FAISS 索引
    index = faiss.read_index(index_path)

    # 3. 读取 query wav
    wav, sr = sf.read(query_wav_path)
    if wav.ndim > 1:
        wav = wav[:, 0]
    duration = len(wav) / sr

    # 4. 切片
    all_query_embs = []
    clip_infos = []  # 记录每段对应原 wav 的 (start_sample, end_sample)
    num_windows = int(np.floor((duration - window_size) / hop_size)) + 1
    for i in range(num_windows):
        start_time_sec = i * hop_size
        start_sample = int(start_time_sec * sr)
        end_sample = start_sample + int(window_size * sr)

        if end_sample > len(wav):
            clip = wav[start_sample:].copy()
            pad_len = end_sample - len(wav)
            clip = np.concatenate([clip, np.zeros(pad_len, dtype=wav.dtype)])
        else:
            clip = wav[start_sample:end_sample]

        emb = _embed_clip(clip, sr)
        all_query_embs.append(emb)
        clip_infos.append((start_time_sec, window_size))

    if not all_query_embs:
        logger.warning("[ERROR] query_slices.too_short window_size=%s hop_size=%s", window_size, hop_size)
        return []

    xb = np.stack(all_query_embs, axis=0).astype("float32")  # (N, D)

    # 5. FAISS search
    D, I = index.search(xb, top_k)  # I: (N, top_k), D: (N, top_k) 相似度值
    results = []
    for i in range(xb.shape[0]):  # 对每个切片
        slice_results = []
        for j in range(top_k):
            idx = int(I[i, j])        # 在 slice_map 中的下标
            score = float(D[i, j])    # 相似度
            info = slice_map[idx]     # {"filename": "...", "start_time": ...}
            slice_results.append((
                info["filename"],
                info["start_time"],
                score
            ))
        results.append(slice_results)

    # 返回一个形如 [ [(f1, t1, s1)], [(f2, t2, s2)], ... ] 的列表
    return results