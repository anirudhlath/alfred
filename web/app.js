// Alfred PWA — WebSocket client for voice + chat
// Connects to the Alfred web channel server via WebSocket.

const chatLog = document.getElementById('chat-log');
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const voiceBtn = document.getElementById('voice-btn');
const statusEl = document.getElementById('status');
const statusLabel = statusEl.querySelector('.status-label');

let ws = null;
let reconnectDelay = 1000;

// --- WebSocket ---

function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        statusEl.classList.add('connected');
        statusLabel.textContent = 'Connected';
        reconnectDelay = 1000;
    };

    ws.onclose = () => {
        statusEl.classList.remove('connected');
        statusLabel.textContent = 'Reconnecting';
        setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 1.5, 10000);
    };

    ws.onerror = () => {
        ws.close();
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'transcription') {
            // Replace the "Voice message" placeholder with actual transcription
            const placeholder = document.getElementById('voice-placeholder');
            if (placeholder) {
                placeholder.textContent = data.text;
                placeholder.removeAttribute('id');
            }
            return;
        }
        if (data.type === 'notification') {
            showNotification(data.title, data.body, data.urgency);
            if (data.audio) playAudio(data.audio);
            return;
        }
        if (data.type === 'voice_notification') {
            if (data.audio) playAudio(data.audio);
            return;
        }
        removeTypingIndicator();
        if (data.type === 'response') {
            appendMessage('alfred', data.text);
            if (data.audio) playAudio(data.audio);
        }
    };
}

// --- TTS Audio Playback ---

// AudioContext must be unlocked by user gesture before playback works
let audioCtx = null;
let audioUnlocked = false;

function unlockAudio() {
    if (audioUnlocked) return;
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    // Resume context if suspended (autoplay policy)
    if (audioCtx.state === 'suspended') audioCtx.resume();
    audioUnlocked = true;
}

// Unlock on first user interaction
document.addEventListener('click', unlockAudio, { once: true });
document.addEventListener('keydown', unlockAudio, { once: true });

function playAudio(base64Audio) {
    if (!audioCtx) unlockAudio();
    const binaryStr = atob(base64Audio);
    const bytes = new Uint8Array(binaryStr.length);
    for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);

    audioCtx.decodeAudioData(bytes.buffer, (buffer) => {
        const source = audioCtx.createBufferSource();
        source.buffer = buffer;
        source.connect(audioCtx.destination);
        source.start(0);
    }, (err) => {
        console.error('Audio decode failed:', err);
    });
}

// --- Messages ---

function clearWelcome() {
    const welcome = chatLog.querySelector('.welcome-message');
    if (welcome) {
        welcome.style.opacity = '0';
        welcome.style.transition = 'opacity 0.3s ease';
        setTimeout(() => welcome.remove(), 300);
    }
}

function appendMessage(role, text, bubbleId) {
    clearWelcome();

    const msg = document.createElement('div');
    msg.className = `message ${role}`;

    const label = document.createElement('div');
    label.className = 'message-label';
    label.textContent = role === 'alfred' ? 'Alfred' : 'You';

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.textContent = text;
    if (bubbleId) bubble.id = bubbleId;

    msg.appendChild(label);
    msg.appendChild(bubble);
    chatLog.appendChild(msg);
    chatLog.scrollTop = chatLog.scrollHeight;
}

function showTypingIndicator() {
    removeTypingIndicator();
    const indicator = document.createElement('div');
    indicator.className = 'message alfred';
    indicator.id = 'typing';

    const label = document.createElement('div');
    label.className = 'message-label';
    label.textContent = 'Alfred';

    const dots = document.createElement('div');
    dots.className = 'typing-indicator';
    dots.innerHTML = '<span></span><span></span><span></span>';

    indicator.appendChild(label);
    indicator.appendChild(dots);
    chatLog.appendChild(indicator);
    chatLog.scrollTop = chatLog.scrollHeight;
}

function removeTypingIndicator() {
    const el = document.getElementById('typing');
    if (el) el.remove();
}

function showNotification(title, body, urgency) {
    clearWelcome();

    const msg = document.createElement('div');
    msg.className = `message alfred notification ${urgency || ''}`;

    const label = document.createElement('div');
    label.className = 'message-label';
    label.textContent = urgency === 'urgent' ? 'Alfred — Urgent' : 'Alfred — Notice';

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble notification-bubble';
    bubble.innerHTML = `<strong>${title}</strong><br>${body}`;

    msg.appendChild(label);
    msg.appendChild(bubble);
    chatLog.appendChild(msg);
    chatLog.scrollTop = chatLog.scrollHeight;
}

// --- Send ---

function send() {
    const text = chatInput.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

    appendMessage('user', text);
    ws.send(JSON.stringify({ type: 'text', content: text, identity: 'sir' }));
    chatInput.value = '';
    autoResizeInput();
    showTypingIndicator();
}

sendBtn.addEventListener('click', send);

chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
    }
});

// Auto-resize textarea
function autoResizeInput() {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
}

chatInput.addEventListener('input', autoResizeInput);

// --- Voice: toggle mode (click to start, click to stop) ---

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

voiceBtn.addEventListener('click', toggleRecording);

async function toggleRecording() {
    if (isRecording) {
        stopRecording();
    } else {
        await startRecording();
    }
}

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
        audioChunks = [];

        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) audioChunks.push(e.data);
        };

        mediaRecorder.onstop = async () => {
            const blob = new Blob(audioChunks, { type: 'audio/webm' });
            stream.getTracks().forEach(t => t.stop());

            // Send audio as base64 over WebSocket
            const reader = new FileReader();
            reader.onload = () => {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    appendMessage('user', '\u266a Voice message', 'voice-placeholder');
                    ws.send(JSON.stringify({
                        type: 'audio',
                        content: reader.result,
                        identity: 'sir',
                    }));
                    showTypingIndicator();
                }
            };
            reader.readAsDataURL(blob);
        };

        mediaRecorder.start();
        isRecording = true;
        voiceBtn.classList.add('recording');
    } catch (err) {
        console.error('Microphone access denied:', err);
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
    }
    isRecording = false;
    voiceBtn.classList.remove('recording');
}

// --- Onboarding ---

function initOnboarding() {
    if (localStorage.getItem('alfred_onboarded')) return;

    const overlay = document.getElementById('onboarding');
    overlay.style.display = 'flex';

    // Step navigation
    overlay.querySelectorAll('[data-next]').forEach(btn => {
        btn.addEventListener('click', () => {
            const target = parseInt(btn.dataset.next, 10);
            showStep(target);
        });
    });

    // Skip buttons — submit defaults and jump to completion
    overlay.querySelectorAll('[data-skip]').forEach(btn => {
        btn.addEventListener('click', async () => {
            // Always dismiss regardless of API success
            localStorage.setItem('alfred_onboarded', '1');
            showStep(5);
            try {
                await fetch('/api/onboarding', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({}),
                });
            } catch (err) {
                console.error('Onboarding default save failed:', err);
            }
        });
    });

    // Finish button — collect and submit preferences + integration credentials
    document.getElementById('ob-finish').addEventListener('click', async () => {
        const payload = {
            wake_time: document.getElementById('ob-wake-time').value || null,
            work_address: document.getElementById('ob-work-address').value || null,
            dietary_restrictions: document.getElementById('ob-dietary').value || null,
            proactivity_level: document.querySelector('input[name="proactivity"]:checked')?.value || 'moderate',
            guest_controls: Array.from(overlay.querySelectorAll('.onboarding-checks input:checked')).map(cb => cb.value),
        };

        try {
            await fetch('/api/onboarding', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        } catch (err) {
            console.error('Onboarding save failed:', err);
        }

        // Save integration credentials (if any filled in)
        const integrationCards = document.querySelectorAll('#ob-integrations .onboarding-integration');
        for (const card of integrationCards) {
            const name = card.dataset.integration;
            const body = {};
            card.querySelectorAll('[data-field]').forEach(input => {
                if (input.value) body[input.name] = input.value;
            });
            if (Object.keys(body).length > 0) {
                try {
                    await fetch(`/api/integrations/${name}/credentials`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                } catch (err) {
                    console.error(`Failed to save ${name} credentials:`, err);
                }
            }
        }

        localStorage.setItem('alfred_onboarded', '1');
        showStep(5);
    });

    // Load integration schemas for onboarding step 4
    fetch('/api/integrations')
        .then(r => r.json())
        .then(integrations => {
            const container = document.getElementById('ob-integrations');
            integrations.forEach(integration => {
                if (!Object.keys(integration.schema.fields).length) return;
                const div = document.createElement('div');
                div.className = 'onboarding-integration';
                div.dataset.integration = integration.name;

                let fieldsHtml = '';
                for (const [name, field] of Object.entries(integration.schema.fields)) {
                    const inputType = field.field_type === 'password' ? 'password' : 'text';
                    const helpHtml = field.help_text
                        ? `<span class="onboarding-note">${field.help_text}</span>`
                        : '';
                    fieldsHtml += `
                        <label class="onboarding-label">
                            ${field.label}
                            <input type="${inputType}" name="${name}" data-field="${name}"
                                   placeholder="${field.placeholder || ''}" autocomplete="off">
                            ${helpHtml}
                        </label>
                    `;
                }
                div.innerHTML = `<h3>${integration.name.replace(/_/g, ' ')}</h3>${fieldsHtml}`;
                container.appendChild(div);
            });
        })
        .catch(err => console.error('Failed to load integrations for onboarding:', err));

    // Close button
    document.getElementById('ob-close').addEventListener('click', () => {
        overlay.style.opacity = '0';
        overlay.style.transition = 'opacity 0.4s ease';
        setTimeout(() => { overlay.style.display = 'none'; }, 400);
    });
}

function showStep(n) {
    const overlay = document.getElementById('onboarding');
    overlay.querySelectorAll('.onboarding-step').forEach(el => {
        el.style.display = el.dataset.step === String(n) ? 'flex' : 'none';
    });
    overlay.querySelectorAll('.progress-dot').forEach(dot => {
        dot.classList.toggle('active', parseInt(dot.dataset.dot, 10) <= n);
    });
}

// --- Init ---

initOnboarding();
connect();
