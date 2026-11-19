"""Create sample-aligned 24 kHz PCM stems, mix and phrase subtitles."""
import math
import wave
from pathlib import Path

import numpy as np


def waveout(path, samples):
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, 24000, 0, 'NONE', ''))
        out.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())


def stamp(sample):
    ms = round(sample / 24)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'


def mix(project, root, build, frames):
    size = frames * 1000
    voice = np.zeros(size, np.float32)
    fx = np.zeros_like(voice)
    captions = []
    end = 0
    for item in project.get('speech', []):
        start = item['frame'] * 1000
        with wave.open(str(root / item['wav']), 'rb') as src:
            if (src.getframerate(), src.getnchannels(), src.getsampwidth()) != (24000, 1, 2):
                raise ValueError('Use Kokoro 24 kHz mono PCM16 WAV')
            audio = np.frombuffer(src.readframes(src.getnframes()), '<i2') / 32768
        if start < end or start + len(audio) > size or not len(audio):
            raise ValueError('Speech overlap, empty passage or insufficient picture duration')
        end = start + len(audio)
        voice[start:end] = audio
        captions.append(f"{len(captions)+1}\n{stamp(start)} --> {stamp(end)}\n{item['text']}\n")
    for cue in project.get('effects', []):
        start = cue['frame'] * 1000
        count = round(24000 * cue['duration'])
        if start < 0 or count < 1 or start + count > size:
            raise ValueError('Effect outside timeline')
        t = np.arange(count) / 24000
        phase = 2 * math.pi
        if cue['kind'] == 'creak':
            tone = np.sin(phase*(210*t+105*t*t)+2*np.sin(phase*17*t))
        elif cue['kind'] == 'tap':
            tone = np.sin(phase*97*t) * np.exp(-28*t)
        elif cue['kind'] == 'air':
            noise = np.random.default_rng(start).normal(0, 0.5, count)
            tone = np.convolve(noise, np.ones(12)/12, mode='full')[:count]
        else:
            raise ValueError(f"Unknown effect {cue['kind']}; author its synthesizer first")
        envelope = np.minimum(1,t/.008)*np.minimum(1,(count/24000-t)/.024)
        fx[start:start+count] += cue.get('gain', .14)*tone*envelope
    mixed = voice + fx*np.where(np.abs(voice)>.015,.42,.78)
    mixed *= min(1,.92/max(.001,float(np.abs(mixed).max())))
    for name, samples in [('speech',voice),('effects',fx),('mix',mixed)]:
        waveout(build / (name+'.wav'), samples)
    (build/'subtitles.srt').write_text('\n'.join(captions),encoding='utf8')
