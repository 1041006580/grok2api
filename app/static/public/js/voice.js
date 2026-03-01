(() => {
  let Room;
  let createLocalTracks;
  let RoomEvent;
  let Track;
  let room = null;
  let visualizerTimer = null;

  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const statusText = document.getElementById('statusText');
  const logContainer = document.getElementById('log');
  const voiceSelect = document.getElementById('voiceSelect');
  const personalitySelect = document.getElementById('personalitySelect');
  const speedRange = document.getElementById('speedRange');
  const speedValue = document.getElementById('speedValue');
  const statusVoice = document.getElementById('statusVoice');
  const statusPersonality = document.getElementById('statusPersonality');
  const statusSpeed = document.getElementById('statusSpeed');
  const audioRoot = document.getElementById('audioRoot');
  const copyLogBtn = document.getElementById('copyLogBtn');
  const clearLogBtn = document.getElementById('clearLogBtn');
  const visualizer = document.getElementById('visualizer');
  const transcriptContainer = document.getElementById('transcript');
  const clearTranscriptBtn = document.getElementById('clearTranscriptBtn');
  const textInputRow = document.getElementById('textInputRow');
  const textInput = document.getElementById('textInput');
  const sendTextBtn = document.getElementById('sendTextBtn');
  const micToggleBtn = document.getElementById('micToggleBtn');
  const micOnIcon = document.getElementById('micOnIcon');
  const micOffIcon = document.getElementById('micOffIcon');

  // item_id -> { element, text } mapping for updating transcript bubbles
  const segmentElements = new Map();
  let localParticipantIdentity = null;
  // Track the last user speech bubble so the completed transcription can update it
  let lastUserSpeechEntry = null;

  function log(message, level = 'info') {
    if (!logContainer) {
      return;
    }
    const p = document.createElement('p');
    const time = new Date().toLocaleTimeString();
    p.textContent = `[${time}] ${message}`;
    if (level === 'error') {
      p.classList.add('log-error');
    } else if (level === 'warn') {
      p.classList.add('log-warn');
    }
    logContainer.prepend(p);
    if (typeof console !== 'undefined') {
      console.log(message);
    }
  }

  function toast(message, type) {
    if (typeof showToast === 'function') {
      showToast(message, type);
    } else {
      log(message, type === 'error' ? 'error' : 'info');
    }
  }

  function setStatus(state, text) {
    if (!statusText) {
      return;
    }
    statusText.textContent = text;
    statusText.classList.remove('connected', 'connecting', 'error');
    if (state) {
      statusText.classList.add(state);
    }
  }

  function setButtons(connected) {
    if (!startBtn || !stopBtn) {
      return;
    }
    if (connected) {
      startBtn.classList.add('hidden');
      stopBtn.classList.remove('hidden');
    } else {
      startBtn.classList.remove('hidden');
      stopBtn.classList.add('hidden');
      startBtn.disabled = false;
    }
  }

  function updateMeta() {
    if (statusVoice) {
      statusVoice.textContent = voiceSelect.value;
    }
    if (statusPersonality) {
      statusPersonality.textContent = personalitySelect.value;
    }
    if (statusSpeed) {
      statusSpeed.textContent = `${speedRange.value}x`;
    }
  }

  function initLiveKit() {
    const lk = window.LiveKitClient || window.LivekitClient;
    if (!lk) {
      return false;
    }
    Room = lk.Room;
    createLocalTracks = lk.createLocalTracks;
    RoomEvent = lk.RoomEvent;
    Track = lk.Track;
    return true;
  }

  function ensureLiveKit() {
    if (Room) {
      return true;
    }
    if (!initLiveKit()) {
      log('错误: LiveKit SDK 未能正确加载，请刷新页面重试', 'error');
      toast('LiveKit SDK 加载失败', 'error');
      return false;
    }
    return true;
  }

  function ensureMicSupport() {
    const hasMediaDevices = typeof navigator !== 'undefined' && navigator.mediaDevices;
    const hasGetUserMedia = hasMediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function';
    if (hasGetUserMedia) {
      return true;
    }
    const isLocalhost = ['localhost', '127.0.0.1'].includes(window.location.hostname);
    const secureHint = window.isSecureContext || isLocalhost
      ? '请使用最新版浏览器并允许麦克风权限'
      : '请使用 HTTPS 或在本机 localhost 访问';
    throw new Error(`当前环境不支持麦克风权限，${secureHint}`);
  }

  // ---- Transcript ----

  function clearTranscript() {
    if (!transcriptContainer) return;
    transcriptContainer.innerHTML = '<div class="transcript-empty">开始会话后，对话内容将实时显示在这里。</div>';
    segmentElements.clear();
    lastUserSpeechEntry = null;
  }

  function hideTranscriptEmpty() {
    if (!transcriptContainer) return;
    const empty = transcriptContainer.querySelector('.transcript-empty');
    if (empty) empty.remove();
  }

  function scrollTranscript() {
    if (!transcriptContainer) return;
    transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
  }

  function handleTranscription(segments, participant) {
    if (!transcriptContainer || !segments || !segments.length) return;
    hideTranscriptEmpty();

    const isLocal = participant && localParticipantIdentity &&
      participant.identity === localParticipantIdentity;
    const role = isLocal ? 'user' : 'assistant';
    const roleLabel = isLocal ? '你' : 'Grok';

    for (const seg of segments) {
      const id = seg.id;
      const text = seg.text || '';
      const isFinal = seg.final;

      let existing = segmentElements.get(id);
      if (existing) {
        const bubble = existing.element.querySelector('.transcript-bubble');
        if (bubble) {
          bubble.textContent = text;
          if (isFinal) {
            bubble.classList.remove('interim');
          }
        }
        existing.text = text;
      } else {
        const msg = document.createElement('div');
        msg.className = `transcript-msg ${role}`;
        msg.dataset.segmentId = id;

        const label = document.createElement('div');
        label.className = 'transcript-role';
        label.textContent = roleLabel;

        const bubble = document.createElement('div');
        bubble.className = 'transcript-bubble';
        if (!isFinal) bubble.classList.add('interim');
        bubble.textContent = text;

        msg.appendChild(label);
        msg.appendChild(bubble);
        transcriptContainer.appendChild(msg);

        const entry = { element: msg, text: text };
        segmentElements.set(id, entry);

        // Track last user speech bubble for later correction by completed transcription
        if (isLocal) {
          lastUserSpeechEntry = entry;
        }
      }
    }
    scrollTranscript();
  }

  function appendTranscriptBubble(id, role, roleLabel, text, interim) {
    hideTranscriptEmpty();
    const msg = document.createElement('div');
    msg.className = `transcript-msg ${role}`;
    msg.dataset.segmentId = id;

    const label = document.createElement('div');
    label.className = 'transcript-role';
    label.textContent = roleLabel;

    const bubble = document.createElement('div');
    bubble.className = 'transcript-bubble';
    if (interim) bubble.classList.add('interim');
    bubble.textContent = text;

    msg.appendChild(label);
    msg.appendChild(bubble);
    transcriptContainer.appendChild(msg);
    segmentElements.set(id, { element: msg, text: text });
    scrollTranscript();
  }

  function handleDataMessage(text) {
    if (!transcriptContainer || !text) return;
    try {
      const data = JSON.parse(text);
      if (!data || !data.type) return;

      const type = data.type;

      // User speech transcript (final corrected version from Grok)
      if (type === 'conversation.item.input_audio_transcription.completed') {
        const transcript = data.transcript || '';
        if (!transcript) return;
        const id = data.item_id || ('user-' + Date.now());
        const existing = segmentElements.get(id);
        if (existing) {
          // Update existing entry matched by item_id
          const bubble = existing.element.querySelector('.transcript-bubble');
          if (bubble) {
            bubble.textContent = transcript;
            bubble.classList.remove('interim');
          }
          existing.text = transcript;
        } else if (lastUserSpeechEntry) {
          // Update the last user speech bubble from TranscriptionReceived
          // (IDs differ between LiveKit segments and Grok item_ids)
          const bubble = lastUserSpeechEntry.element.querySelector('.transcript-bubble');
          if (bubble) {
            bubble.textContent = transcript;
            bubble.classList.remove('interim');
          }
          lastUserSpeechEntry.text = transcript;
          segmentElements.set(id, lastUserSpeechEntry);
          lastUserSpeechEntry = null;
        } else {
          appendTranscriptBubble(id, 'user', '你', transcript, false);
        }
        return;
      }

      // Grok response streaming delta
      if (type === 'response.audio_transcript.delta') {
        const delta = data.delta || '';
        if (!delta) return;
        const id = data.item_id || ('grok-' + Date.now());
        const existing = segmentElements.get(id);
        if (existing) {
          existing.text += delta;
          const bubble = existing.element.querySelector('.transcript-bubble');
          if (bubble) bubble.textContent = existing.text;
        } else {
          appendTranscriptBubble(id, 'assistant', 'Grok', delta, true);
        }
        scrollTranscript();
        return;
      }

      // Grok response complete
      if (type === 'response.audio_transcript.done') {
        const transcript = data.transcript || '';
        const id = data.item_id || ('grok-' + Date.now());
        const existing = segmentElements.get(id);
        if (existing) {
          if (transcript) {
            existing.text = transcript;
            const bubble = existing.element.querySelector('.transcript-bubble');
            if (bubble) bubble.textContent = transcript;
          }
          const bubble = existing.element.querySelector('.transcript-bubble');
          if (bubble) bubble.classList.remove('interim');
        } else if (transcript) {
          appendTranscriptBubble(id, 'assistant', 'Grok', transcript, false);
        }
        return;
      }
    } catch (e) {
      // Not JSON, ignore
    }
  }

  // ---- Text message ----

  function sendTextMessage(text) {
    if (!text.trim()) return;
    const msg = text.trim();

    if (!room || room.state !== 'connected') {
      toast('请先连接语音会话', 'error');
      return;
    }

    // Show user message in transcript
    const userId = 'text-user-' + Date.now();
    appendTranscriptBubble(userId, 'user', '你', msg, false);

    try {
      // Send text via LiveKit data channel with topic "grok.chat"
      // Grok agent will respond with both audio and transcript
      const payload = new TextEncoder().encode(msg);
      room.localParticipant.publishData(payload, { topic: 'grok.chat' });
      log(`文字已发送: ${msg}`);
    } catch (err) {
      log(`文字发送失败: ${err.message}`, 'error');
      toast('文字发送失败', 'error');
    }
  }

  function setTextInputVisible(visible) {
    if (!textInputRow) return;
    if (visible) {
      textInputRow.classList.remove('hidden');
    } else {
      textInputRow.classList.add('hidden');
      if (textInput) textInput.value = '';
    }
  }

  function updateSendBtn() {
    if (!sendTextBtn || !textInput) return;
    sendTextBtn.disabled = !textInput.value.trim();
  }

  // ---- Microphone toggle ----

  function toggleMic() {
    if (!room || room.state !== 'connected') return;
    const enabled = room.localParticipant.isMicrophoneEnabled;
    room.localParticipant.setMicrophoneEnabled(!enabled);
    updateMicUI(!enabled);
  }

  function updateMicUI(enabled) {
    if (!micToggleBtn) return;
    if (enabled) {
      micToggleBtn.classList.remove('muted');
      micToggleBtn.title = '关闭麦克风';
      if (micOnIcon) micOnIcon.classList.remove('hidden');
      if (micOffIcon) micOffIcon.classList.add('hidden');
    } else {
      micToggleBtn.classList.add('muted');
      micToggleBtn.title = '开启麦克风';
      if (micOnIcon) micOnIcon.classList.add('hidden');
      if (micOffIcon) micOffIcon.classList.remove('hidden');
    }
  }

  // ---- Session ----

  async function startSession() {
    if (!ensureLiveKit()) {
      return;
    }

    try {
      const authHeader = await ensurePublicKey();
      if (authHeader === null) {
        toast('请先配置 Public Key', 'error');
        window.location.href = '/login';
        return;
      }

      startBtn.disabled = true;
      updateMeta();
      setStatus('connecting', '正在连接');
      clearTranscript();

      // Request mic permission early — browsers only expose full WebRTC ICE
      // candidates (all network interfaces) after media permission is granted.
      // Without this, ICE negotiation may fail due to insufficient candidates.
      log('正在请求麦克风权限...');
      ensureMicSupport();
      const localTracks = await createLocalTracks({ audio: true, video: false });
      log('麦克风权限已获取');

      log('正在获取 Token...');

      const params = new URLSearchParams({
        voice: voiceSelect.value,
        personality: personalitySelect.value,
        speed: speedRange.value
      });

      const headers = buildAuthHeaders(authHeader);

      const response = await fetch(`/v1/public/voice/token?${params.toString()}`, {
        headers
      });

      if (!response.ok) {
        throw new Error(`获取 Token 失败: ${response.status}`);
      }

      const { token, url } = await response.json();
      log(`获取 Token 成功 (${voiceSelect.value}, ${personalitySelect.value}, ${speedRange.value}x)`);

      room = new Room({
        adaptiveStream: true,
        dynacast: true,
        // Longer timeout for mobile networks with slower ICE negotiation
        peerConnectionTimeout: 30000
      });

      room.on(RoomEvent.ParticipantConnected, (p) => log(`参与者已连接: ${p.identity}`));
      room.on(RoomEvent.ParticipantDisconnected, (p) => log(`参与者已断开: ${p.identity}`));
      room.on(RoomEvent.TrackSubscribed, (track) => {
        log(`订阅音轨: ${track.kind}`);
        if (track.kind === Track.Kind.Audio) {
          const element = track.attach();
          // iOS Safari requires playsinline and may need explicit play()
          element.setAttribute('playsinline', '');
          element.setAttribute('autoplay', '');
          if (audioRoot) {
            audioRoot.appendChild(element);
          } else {
            document.body.appendChild(element);
          }
          // iOS often blocks autoplay — retry on user-gesture context
          element.play().catch(() => {
            log('音频自动播放被阻止，点击页面任意位置恢复', 'warn');
            const resume = () => {
              element.play().catch(() => {});
              document.removeEventListener('touchstart', resume);
              document.removeEventListener('click', resume);
            };
            document.addEventListener('touchstart', resume, { once: true });
            document.addEventListener('click', resume, { once: true });
          });
        }
      });

      room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
        handleTranscription(segments, participant);
      });

      room.on(RoomEvent.DataReceived, (payload, participant, kind, topic) => {
        let text = '';
        try {
          text = new TextDecoder().decode(payload);
        } catch (e) {
          return;
        }
        handleDataMessage(text);
      });

      room.on(RoomEvent.Disconnected, (reason) => {
        log(`已断开连接${reason ? ': ' + reason : ''}`);
        resetUI();
      });

      log('正在连接: ' + url);
      await room.connect(url, token);
      localParticipantIdentity = room.localParticipant.identity;
      log('已连接到 LiveKit 服务器');

      setStatus('connected', '通话中');
      setButtons(true);
      setTextInputVisible(true);

      log('正在发布麦克风音轨...');
      for (const track of localTracks) {
        await room.localParticipant.publishTrack(track);
      }
      log('语音已开启');
      toast('语音连接成功', 'success');
    } catch (err) {
      const message = err && err.message ? err.message : '连接失败';
      log(`错误: ${message}`, 'error');
      toast(message, 'error');
      setStatus('error', '连接错误');
      startBtn.disabled = false;
    }
  }

  async function stopSession() {
    if (room) {
      await room.disconnect();
    }
    resetUI();
  }

  function resetUI() {
    setStatus('', '未连接');
    setButtons(false);
    setTextInputVisible(false);
    updateMicUI(true);
    if (audioRoot) {
      audioRoot.innerHTML = '';
    }
    localParticipantIdentity = null;
  }

  function clearLog() {
    if (logContainer) {
      logContainer.innerHTML = '';
    }
  }

  async function copyLog() {
    if (!logContainer) {
      return;
    }
    const lines = Array.from(logContainer.querySelectorAll('p'))
      .map((p) => p.textContent)
      .join('\n');
    try {
      await navigator.clipboard.writeText(lines);
      toast('日志已复制', 'success');
    } catch (err) {
      toast('复制失败，请手动选择', 'error');
    }
  }

  speedRange.addEventListener('input', (e) => {
    speedValue.textContent = Number(e.target.value).toFixed(1);
    const min = Number(speedRange.min || 0);
    const max = Number(speedRange.max || 100);
    const val = Number(speedRange.value || 0);
    const pct = ((val - min) / (max - min)) * 100;
    speedRange.style.setProperty('--range-progress', `${pct}%`);
    updateMeta();
  });

  voiceSelect.addEventListener('change', updateMeta);
  personalitySelect.addEventListener('change', updateMeta);

  startBtn.addEventListener('click', startSession);
  stopBtn.addEventListener('click', stopSession);
  if (copyLogBtn) {
    copyLogBtn.addEventListener('click', copyLog);
  }
  if (clearLogBtn) {
    clearLogBtn.addEventListener('click', clearLog);
  }
  if (clearTranscriptBtn) {
    clearTranscriptBtn.addEventListener('click', clearTranscript);
  }
  if (textInput) {
    textInput.addEventListener('input', updateSendBtn);
    textInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && textInput.value.trim()) {
        e.preventDefault();
        sendTextMessage(textInput.value);
        textInput.value = '';
        updateSendBtn();
      }
    });
  }
  if (sendTextBtn) {
    sendTextBtn.addEventListener('click', () => {
      if (textInput && textInput.value.trim()) {
        sendTextMessage(textInput.value);
        textInput.value = '';
        updateSendBtn();
      }
    });
  }
  if (micToggleBtn) {
    micToggleBtn.addEventListener('click', toggleMic);
  }

  speedValue.textContent = Number(speedRange.value).toFixed(1);
  {
    const min = Number(speedRange.min || 0);
    const max = Number(speedRange.max || 100);
    const val = Number(speedRange.value || 0);
    const pct = ((val - min) / (max - min)) * 100;
    speedRange.style.setProperty('--range-progress', `${pct}%`);
  }
  function buildVisualizerBars() {
    if (!visualizer) return;
    visualizer.innerHTML = '';
    const targetCount = Math.max(36, Math.floor(visualizer.offsetWidth / 7));
    for (let i = 0; i < targetCount; i += 1) {
      const bar = document.createElement('div');
      bar.className = 'bar';
      visualizer.appendChild(bar);
    }
  }

  window.addEventListener('resize', buildVisualizerBars);
  buildVisualizerBars();
  updateMeta();
  setStatus('', '未连接');

  if (!visualizerTimer) {
    visualizerTimer = setInterval(() => {
      const bars = document.querySelectorAll('.visualizer .bar');
      bars.forEach((bar) => {
        if (statusText && statusText.classList.contains('connected')) {
          bar.style.height = `${Math.random() * 32 + 6}px`;
        } else {
          bar.style.height = '6px';
        }
      });
    }, 150);
  }
})();
