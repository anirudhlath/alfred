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
        removeTypingIndicator();
        if (data.type === 'response') {
            appendMessage('alfred', data.text);
        }
    };
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

function appendMessage(role, text) {
    clearWelcome();

    const msg = document.createElement('div');
    msg.className = `message ${role}`;

    const label = document.createElement('div');
    label.className = 'message-label';
    label.textContent = role === 'alfred' ? 'Alfred' : 'You';

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.textContent = text;

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

// --- Voice: push-to-talk ---

let mediaRecorder = null;
let audioChunks = [];

voiceBtn.addEventListener('mousedown', startRecording);
voiceBtn.addEventListener('touchstart', (e) => {
    e.preventDefault();
    startRecording();
});

voiceBtn.addEventListener('mouseup', stopRecording);
voiceBtn.addEventListener('mouseleave', stopRecording);
voiceBtn.addEventListener('touchend', (e) => {
    e.preventDefault();
    stopRecording();
});

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
                    appendMessage('user', '\u266a Voice message');
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
        voiceBtn.classList.add('recording');
    } catch (err) {
        console.error('Microphone access denied:', err);
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        voiceBtn.classList.remove('recording');
    }
}

// --- Init ---

connect();
