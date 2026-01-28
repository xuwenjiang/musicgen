// frontend/app.js

document.addEventListener("DOMContentLoaded", () => {
  let recorder, audioBlob = null, uploadedFile = null;

  const desc = document.getElementById("description");
  const recBtn = document.getElementById("record-btn");
  const stopBtn = document.getElementById("stop-btn");
  const genBtn = document.getElementById("generate-btn");
  const fileUpload = document.getElementById("file-upload");
  const player = document.getElementById("audio-player");         // 循环录制结果播放器
  const inputPlayer = document.getElementById("input-player");    // 普通录音/上传原始波形播放器
  const generatePlayer = document.getElementById("generate-player"); // 普通生成结果播放器
  const durationInput = document.getElementById("duration");
  const rebuildBtn = document.getElementById("rebuild-btn");
  const rebuildSliceBtn = document.getElementById("rebuild-slice-btn");
  const findBtn = document.getElementById("find-btn");

  // —— 循环录音 相关 DOM —— 
  const startLoopBtn = document.getElementById("start-loop-btn");
  const stopLoopBtn = document.getElementById("stop-loop-btn");

  // —— 与滑块（调整参数）相关 —— 
  const silenceThresholdInput = document.getElementById("silence-threshold");
  const silenceDurationInput = document.getElementById("silence-duration");
  const maxSegmentMsInput = document.getElementById("max-segment-ms");

  // 对应滑块右侧的数值展示 <span> 
  const silenceThresholdDisplay = document.getElementById("silence-threshold-display");
  const silenceDurationDisplay = document.getElementById("silence-duration-display");
  const maxSegmentMsDisplay = document.getElementById("max-segment-ms-display");

  // 在页面载入时，给三个 <input type="range"> 绑定 “实时更新右侧 <span>” 的逻辑——
  silenceThresholdInput.addEventListener("input", () => {
    // 保持三位小数
    silenceThresholdDisplay.textContent = parseFloat(silenceThresholdInput.value).toFixed(3);
  });
  silenceDurationInput.addEventListener("input", () => {
    silenceDurationDisplay.textContent = silenceDurationInput.value;
  });
  maxSegmentMsInput.addEventListener("input", () => {
    maxSegmentMsDisplay.textContent = maxSegmentMsInput.value;
  });

  // —— 到此为止，滑块联动显示部分完成 —— 

  // —— 其它循环录音所需的全局变量 —— 
  let loopStream = null;
  let loopAudioContext = null;
  let loopRecorder = null;
  let analyserNode = null;
  let loopDetectInterval = null;

  let wasSpeaking = false;
  let segmentStartTime = null;
  let silentSince = null;

  function updateGenerateButtonState() {
    genBtn.disabled = !(desc.value.trim() || audioBlob || uploadedFile);
  }

  // -------- 普通：文字描述 + 上传 + 普通录音 + 生成/检索 按钮逻辑（保持原样） -------- 

  desc.addEventListener("input", updateGenerateButtonState);

  // 处理上传文件
  fileUpload.addEventListener("change", () => {
    const file = fileUpload.files[0] || null;
    if (file) {
      uploadedFile = file;
      // 显示上传回放
      const url = URL.createObjectURL(file);
      inputPlayer.src = url;
    } else {
      // 用户清空上传：保留录制音频或无音频
      uploadedFile = null;
      if (!audioBlob) {
        // inputCont.classList.remove("visible");
      }
    }
    updateGenerateButtonState();
  });

  // 开始普通录音
  recBtn.addEventListener("click", async () => {
    recBtn.disabled = true;
    stopBtn.disabled = false;
    audioBlob = null;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioContext.createMediaStreamSource(stream);
    recorder = new Recorder(source, { numChannels: 1 });
    recorder.record();
    console.log("普通录音：开始录制");
  });

  // 停止普通录音并导出 WAV
  stopBtn.addEventListener("click", () => {
    recBtn.disabled = false;
    stopBtn.disabled = true;
    recorder.stop();
    recorder.exportWAV((blob) => {
      audioBlob = blob;
      // 检查录音是否成功
      if (audioBlob.size <= 44) {
        alert("录音失败，请确保麦克风正常工作并重新录音！");
        audioBlob = null;
        return;
      }
      const url = URL.createObjectURL(audioBlob);
      inputPlayer.src = url;
      // inputCont.classList.add("visible");
      updateGenerateButtonState();
      recorder.clear();
    });
  });

  // “生成音乐” 按钮 —— 现在结果推给 #generate-player
  genBtn.addEventListener("click", async () => {
    if (!desc.value.trim() && !audioBlob && !uploadedFile) {
      alert("请输入描述、录制一段音频，或上传一个文件！");
      return;
    }

    const duration = parseInt(durationInput.value, 10) || 5;
    const fd = new FormData();
    fd.append("description", desc.value.trim());
    fd.append("duration", duration);
    if (uploadedFile) {
      fd.append("audio_file", uploadedFile, uploadedFile.name);
    } else if (audioBlob) {
      fd.append("audio_file", audioBlob, "input.wav");
    }
    try {
      console.log("→ POST /generate");
      const resp = await fetch("http://localhost:8000/generate", {
        method: "POST",
        body: fd
      });
      if (!resp.ok) throw new Error(resp.statusText);
      const blob = await resp.blob();

      // **这里把生成的结果直接赋给 #generate-player，保证它就在上半区播放** 
      const urlGen = URL.createObjectURL(blob);
      generatePlayer.src = urlGen;
      generatePlayer.play();
    } catch (e) {
      console.error("generate error:", e);
      alert("生成失败，请重试");
    }
  });

  // 重建索引
  rebuildBtn.addEventListener("click", async () => {
    try {
      const resp = await fetch("http://localhost:8000/rebuild_index", { method: "POST" });
      const info = await resp.json();
      alert(`Index built: ${info.built} files`);
    } catch {
      alert("重建索引失败");
    }
  });

  // 重建切片索引
  rebuildSliceBtn.addEventListener("click", async () => {
    try {
      const resp = await fetch("http://localhost:8000/rebuild_slice_index", {
        method: "POST",
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.error || "unknown");
      }
      const data = await resp.json();
      alert(data.detail || "Slice index rebuilt successfully.");
    } catch (e) {
      console.error("rebuild slice index error:", e);
      alert("重建切片索引失败: " + e.message);
    }
  });

  // 查找相似
  findBtn.addEventListener("click", async () => {
    if (!audioBlob && !uploadedFile) {
      alert("请先录音或上传音频！");
      return;
    }

    const fd = new FormData();
    // 和后端参数名保持一致
    fd.append("audio_file", uploadedFile || audioBlob, "query.wav");

    try {
      console.log("→ POST /find_similar");
      const resp = await fetch("http://localhost:8000/find_similar", {
        method: "POST",
        body: fd
      });
      if (!resp.ok) throw new Error(resp.statusText);
      // 直接拿回音频文件（stream）—— 这里也视为“生成”后的播放
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      generatePlayer.src = url;
      generatePlayer.play();
    } catch (e) {
      console.error("find_similar error:", e);
      alert("检索失败，请重试");
    }
  });

  // -------- 以上为“普通录音 + 生成/检索”逻辑，均未改动 id 以外的核心代码 -------- 

  // =========================================
  // ======= 下面开始“循环录音”逻辑改动 =======
  // =========================================

  startLoopBtn.addEventListener("click", async () => {
    console.log(">>> 开始循环录音");

    // 1) 直接获取麦克风 MediaStream 并构造 AudioContext
    loopStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    loopAudioContext = new (window.AudioContext || window.webkitAudioContext)();
    const loopSource = loopAudioContext.createMediaStreamSource(loopStream);

    // 2) 创建 Recorder.js，立即 record()（从此刻起不断向内部缓存写 PCM）
    loopRecorder = new Recorder(loopSource, { numChannels: 1 });
    loopRecorder.record();

    // 3) 创建 AnalyserNode，fftSize=2048
    analyserNode = loopAudioContext.createAnalyser();
    analyserNode.fftSize = 2048;
    loopSource.connect(analyserNode);

    // 4) 初始化状态
    wasSpeaking = false;
    segmentStartTime = null;
    silentSince = null;

    // 5) 定时检测音量（每 200ms）
    loopDetectInterval = setInterval(() => {
      const dataArray = new Float32Array(analyserNode.fftSize);
      analyserNode.getFloatTimeDomainData(dataArray);

      // 计算 RMS
      let sumSquares = 0;
      for (let i = 0; i < dataArray.length; i++) {
        sumSquares += dataArray[i] * dataArray[i];
      }
      const rms = Math.sqrt(sumSquares / dataArray.length);
      const now = Date.now();

      // —— 这里实时从滑块读取值 —— 
      const SILENCE_THRESHOLD = parseFloat(silenceThresholdInput.value) || 0.01;
      const SILENCE_DURATION = parseInt(silenceDurationInput.value, 10) || 1500;
      const MAX_SEGMENT_MS = parseInt(maxSegmentMsInput.value, 10) || 5000;

      if (rms > SILENCE_THRESHOLD) {
        // 当前帧 是“有声”
        if (!wasSpeaking) {
          // 刚刚从静默切到有声
          wasSpeaking = true;
          segmentStartTime = now;
          silentSince = null;
          console.log("🚀 有声开始，开始新段落");
        } else {
          // 已经在“有声”里，判断是不是超过 MAX_SEGMENT_MS
          const spokenDuration = now - segmentStartTime;
          if (spokenDuration >= MAX_SEGMENT_MS) {
            // 强制切段
            console.log(`⏱ 达到 ${MAX_SEGMENT_MS}ms，强制切段并发送`);
            wasSpeaking = false;
            silentSince = null;
            exportAndSendSegment();
          }
        }
      } else {
        // 当前帧 是“静默”
        if (wasSpeaking) {
          if (!silentSince) {
            // 刚刚从“有声”切到“静默”，记录静默开始时间
            silentSince = now;
          } else {
            const elapsedSilence = now - silentSince;
            if (elapsedSilence >= SILENCE_DURATION) {
              // 静默连续超过阈值 → 段落结束
              console.log(`🔚 段落结束(静默≥${SILENCE_DURATION}ms)，导出并发送`);
              wasSpeaking = false;
              silentSince = null;
              exportAndSendSegment();
            }
          }
        }
      }
    }, 200);

    // 更新按钮状态
    startLoopBtn.disabled = true;
    stopLoopBtn.disabled = false;
  });

  /**
   * 每当“一段录音”要发给后端时，就调用这个函数：
   * 1) loopRecorder.stop() —— 停止 Recorder.js 录制
   * 2) loopRecorder.exportWAV(callback) —— 把本次录的【含静默】导出成 WAV
   * 3) 在 callback 里：
   *    a) 去除段首/段尾静默 → 得到 trimmedBlob
   *    b) loopRecorder.clear() + loopRecorder.record() —— 立即清空缓存并重启下一次录制
   *    c) 构造 FormData 送 trimmedBlob 给后端 → 拿到 returnedBlob → 直接给 “#audio-player” 播放并播放
   */
  async function exportAndSendSegment() {
    if (!loopRecorder) return;
    // 1) 先停止当前 Recorder.js
    loopRecorder.stop();

    loopRecorder.exportWAV(async (blob) => {
      // —— 2) 去静默：decode → 找首尾非静默 → 重新 encode —— 
      let trimmedBlob;
      try {
        trimmedBlob = await trimSilenceFromWav(blob);
      } catch (e) {
        console.warn("去静默失败，使用原始 blob：", e);
        trimmedBlob = blob;
      }

      // —— 3) 清空 + 重新开始录制 
      loopRecorder.clear();
      loopRecorder.record();

      // —— 4) 构造 FormData，发送 trimmedBlob 给后端 —— 
      const fd = new FormData();
      fd.append("audio_file", trimmedBlob, "segment.wav");

      try {
        console.log("→ POST /loop_audio （发送去静默后的 WAV）");
        const resp = await fetch("http://localhost:8000/echo", {
          method: "POST",
          body: fd,
        });
        if (!resp.ok) throw new Error(resp.statusText);

        // —— 4a) 拿到后端返回的音频，给 “#audio-player” 播放并播放 —— 
        const returnedBlob = await resp.blob();
        const urlResult = URL.createObjectURL(returnedBlob);
        player.src = urlResult;
        player.play();
      } catch (err) {
        console.error("loop segment send error:", err);
      }
    });
  }

  stopLoopBtn.addEventListener("click", () => {
    console.log(">>> 停止循环录音");
    clearInterval(loopDetectInterval);
    loopDetectInterval = null;

    if (loopRecorder) {
      loopRecorder.stop();
      loopRecorder.clear();
      loopRecorder = null;
    }
    if (loopStream) {
      loopStream.getTracks().forEach((t) => t.stop());
      loopStream = null;
    }
    if (loopAudioContext) {
      loopAudioContext.close();
      loopAudioContext = null;
    }
    wasSpeaking = false;
    segmentStartTime = null;
    silentSince = null;

    startLoopBtn.disabled = false;
    stopLoopBtn.disabled = true;
  });

  updateGenerateButtonState();

  // -------------------- 辅助函数：去除 WAV 前后静默 --------------------

  /**
   * 接收一个 WAV Blob，使用 AudioContext.decodeAudioData 解码，
   * 找到首尾“超过阈值”的样本索引，然后重新打包成一个新的 WAV Blob 并返回。
   */
  async function trimSilenceFromWav(wavBlob) {
    // 1) 用 AudioContext 解码
    const arrayBuffer = await wavBlob.arrayBuffer();
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    if (ac.state === "suspended") {
      await ac.resume();
    }
    const audioBuffer = await ac.decodeAudioData(arrayBuffer);

    // 2) 拿到单通道数据（只支持 mono）
    const channelData = audioBuffer.getChannelData(0);
    const sampleRate = audioBuffer.sampleRate;

    // 3) 找首个“有声音”样本索引
    let startSample = 0;
    const threshold = parseFloat(silenceThresholdInput.value);
    for (let i = 0; i < channelData.length; i++) {
      if (Math.abs(channelData[i]) > threshold) {
        startSample = i;
        break;
      }
    }
    // 4) 找最后一个“有声音”样本索引
    let endSample = channelData.length;
    for (let i = channelData.length - 1; i >= 0; i--) {
      if (Math.abs(channelData[i]) > threshold) {
        endSample = i + 1;
        break;
      }
    }

    // 全是静默？直接返回原 blob
    if (startSample >= endSample) {
      ac.close?.();
      return wavBlob;
    }

    // 5) 提取“有声”区间的数据到新的 Float32Array
    const trimmedData = channelData.slice(startSample, endSample);

    // 6) 重新打包成 WAV Blob
    const trimmedWavBlob = encodeWAV(trimmedData, sampleRate);
    ac.close?.();
    return trimmedWavBlob;
  }

  /**
   * 给定 Float32Array ([-1,1]) 和采样率，生成一个 WAV Blob（16-bit PCM）。
   */
  function encodeWAV(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);

    /* RIFF identifier */
    writeString(view, 0, "RIFF");
    /* file length */
    view.setUint32(4, 36 + samples.length * 2, true);
    /* WAVE */
    writeString(view, 8, "WAVE");
    /* fmt  chunk */
    writeString(view, 12, "fmt ");
    view.setUint32(16, 16, true);             // Subchunk1Size (16 for PCM)
    view.setUint16(20, 1, true);              // AudioFormat (1 = PCM)
    view.setUint16(22, 1, true);              // NumChannels = 1 (mono)
    view.setUint32(24, sampleRate, true);     // SampleRate
    view.setUint32(28, sampleRate * 2, true); // ByteRate = SampleRate * NumChannels * BitsPerSample/8
    view.setUint16(32, 2, true);              // BlockAlign = NumChannels * BitsPerSample/8
    view.setUint16(34, 16, true);             // BitsPerSample = 16
    /* data chunk */
    writeString(view, 36, "data");
    view.setUint32(40, samples.length * 2, true);

    // PCM 16-bit
    let offset = 44;
    for (let i = 0; i < samples.length; i++) {
      let s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
      offset += 2;
    }

    return new Blob([view], { type: "audio/wav" });
  }

  function writeString(view, offset, str) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  }
});
