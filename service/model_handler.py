import torch
from audiocraft.models import MusicGen
import soundfile as sf
from audio_utils import load_audio
import tempfile
import time

from logging_utils import get_logger


logger = get_logger("model")

# 加载模型
device = "cuda" if torch.cuda.is_available() else "cpu"
start_time = time.time()
logger.info("[MODEL] load.start model=facebook/musicgen-melody device=%s", device)
model = MusicGen.get_pretrained("facebook/musicgen-melody", device=device)
logger.info("[MODEL] load.end elapsed_ms=%.2f", (time.time() - start_time) * 1000)

def generate_audio(description: str, audio_path: str, duration: int) -> bytes:
    logger.info("[STEP] generate_audio.start duration=%s has_audio=%s", duration, audio_path is not None)
    melody = None
    if audio_path:
        logger.info("[STEP] load_audio.start path=%s", audio_path)
        melody, sr = load_audio(audio_path, model.sample_rate)
        logger.info("[STEP] load_audio.end shape=%s sample_rate=%s", tuple(melody.shape), sr)

    model.set_generation_params(duration=duration)
    logger.info("[STEP] generation_params duration=%s sample_rate=%s", duration, model.sample_rate)

    if melody is not None:
        logger.info("[STEP] generate.mode=with_chroma")
        wav = model.generate_with_chroma(
            descriptions=[description],
            melody_wavs=melody,
            melody_sample_rate=sr,
            progress=False
        )[0]
    else:
        logger.info("[STEP] generate.mode=text_only")
        wav = model.generate([description], progress=False)[0]

    logger.info("[STEP] generate_audio.render_complete")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_wav:
        sf.write(temp_wav.name, wav.cpu().numpy().T, samplerate=model.sample_rate)
        temp_wav.seek(0)
        audio_data = temp_wav.read()
    logger.info("[STEP] generate_audio.wav_written bytes=%s temp_file=%s", len(audio_data), temp_wav.name)
    return audio_data