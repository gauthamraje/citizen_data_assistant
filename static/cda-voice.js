/**
 * Browser voice I/O — no ElevenLabs or extra API keys.
 * Speech-to-text: Web Speech API (mic → text → sent to Claude).
 * Text-to-speech: speechSynthesis (Claude reply read aloud).
 */
(function (global) {
    'use strict';

    const SpeechRecognition =
        global.SpeechRecognition || global.webkitSpeechRecognition;

    let voiceEnabled = false;
    let voiceMode = false;
    let listening = false;
    let recognition = null;

    let micBtn = null;
    let voiceModeBtn = null;
    let onTranscript = null;

    function detectSupport() {
        const hasStt = !!SpeechRecognition;
        const hasTts = typeof global.speechSynthesis !== 'undefined';
        return hasStt || hasTts;
    }

    function checkStatus() {
        voiceEnabled = detectSupport();
        updateUi();
        return Promise.resolve(voiceEnabled);
    }

    function configure(hooks) {
        micBtn = hooks.micBtn || null;
        voiceModeBtn = hooks.voiceModeBtn || null;
        onTranscript = hooks.onTranscript || null;
    }

    function updateUi() {
        const hasStt = !!SpeechRecognition;
        const hasTts = typeof global.speechSynthesis !== 'undefined';

        if (micBtn) {
            micBtn.style.display = hasStt ? 'flex' : 'none';
            micBtn.title = hasStt
                ? 'Tap to speak — converts to text for Claude'
                : 'Speech input not supported in this browser';
        }
        if (voiceModeBtn) {
            voiceModeBtn.style.display = hasTts ? 'flex' : 'none';
            voiceModeBtn.classList.toggle('active', voiceMode);
            voiceModeBtn.title = voiceMode
                ? 'Voice mode ON — Claude replies read aloud'
                : 'Voice mode OFF — tap to hear replies';
        }
    }

    function toggleVoiceMode() {
        if (!voiceEnabled) return;
        voiceMode = !voiceMode;
        if (!voiceMode && global.speechSynthesis) {
            global.speechSynthesis.cancel();
        }
        updateUi();
    }

    function isVoiceMode() {
        return voiceMode && voiceEnabled;
    }

    function plainTextForSpeech(text) {
        return (text || '')
            .replace(/!\[[^\]]*\]\([^\)]*\)/g, '')
            .replace(/\*\*/g, '')
            .replace(/---[\s\S]*/g, '')
            .replace(/【[^】]*】/g, '')
            .replace(/[#*_>`]/g, '')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 2000);
    }

    function speakText(text) {
        if (!global.speechSynthesis) return Promise.resolve();
        const plain = plainTextForSpeech(text);
        if (!plain) return Promise.resolve();

        global.speechSynthesis.cancel();
        return new Promise((resolve) => {
            const utter = new SpeechSynthesisUtterance(plain);
            utter.rate = 1;
            utter.pitch = 1;
            // Prefer an English voice when available (works for Hinglish too).
            const voices = global.speechSynthesis.getVoices();
            const en =
                voices.find((v) => v.lang.startsWith('en') && v.localService) ||
                voices.find((v) => v.lang.startsWith('en'));
            if (en) utter.voice = en;
            utter.onend = resolve;
            utter.onerror = resolve;
            global.speechSynthesis.speak(utter);
        });
    }

    function startListening() {
        if (!SpeechRecognition || listening) return;

        recognition = new SpeechRecognition();
        recognition.lang = 'en-IN';
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;
        recognition.continuous = false;

        recognition.onstart = () => {
            listening = true;
            if (micBtn) micBtn.classList.add('recording');
        };

        recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript.trim();
            if (transcript && onTranscript) onTranscript(transcript);
        };

        recognition.onerror = (event) => {
            console.warn('Speech recognition error', event.error);
            if (event.error === 'not-allowed') {
                alert('Microphone permission is needed for voice input.');
            } else if (event.error !== 'aborted') {
                alert('Could not hear you clearly — please try again or type.');
            }
            stopListening();
        };

        recognition.onend = () => stopListening();

        try {
            recognition.start();
        } catch (err) {
            console.error(err);
            stopListening();
        }
    }

    function stopListening() {
        listening = false;
        if (micBtn) micBtn.classList.remove('recording');
        if (recognition) {
            try {
                recognition.stop();
            } catch {
                /* already stopped */
            }
            recognition = null;
        }
    }

    function toggleMicPress() {
        if (!SpeechRecognition) {
            alert('Voice input is not supported in this browser. Try Chrome or Edge.');
            return;
        }
        if (listening) stopListening();
        else startListening();
    }

    function addListenButton(bubbleDiv, text) {
        if (!global.speechSynthesis) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'listen-btn';
        btn.title = 'Listen to this message';
        btn.innerHTML =
            '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07"/></svg> Listen';
        btn.onclick = () => speakText(text);
        bubbleDiv.appendChild(btn);
    }

    async function maybeSpeakAssistantReply(text) {
        if (isVoiceMode()) await speakText(text);
    }

    // Voices load asynchronously in some browsers.
    if (global.speechSynthesis) {
        global.speechSynthesis.onvoiceschanged = () => updateUi();
    }

    global.CDAVoice = {
        configure,
        checkStatus,
        toggleMicPress,
        toggleVoiceMode,
        isVoiceMode,
        speakText,
        maybeSpeakAssistantReply,
        addListenButton,
        isEnabled: () => voiceEnabled,
    };
})(window);
