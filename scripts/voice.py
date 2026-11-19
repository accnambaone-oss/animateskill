"""Generate phrase WAVs and frame-aligned caption timings with local Kokoro."""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--lang", default="a")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    model = KPipeline(lang_code=args.lang)
    passages = []
    frame = 0
    for index, line in enumerate(json.loads(args.script.read_text(encoding="utf8"))):
        pieces = [np.asarray(audio) for _, _, audio in model(
            line["text"], voice=line.get("voice", "af_heart"), speed=line.get("speed", 1.0))]
        if not pieces:
            raise ValueError(f"No audio for passage {index}")
        audio = np.concatenate(pieces)
        target = args.output / f"line-{index:04}.wav"
        sf.write(target, audio, 24000, subtype="PCM_16")
        passages.append(dict(frame=frame, wav=target.name, text=line["text"]))
        frame += math.ceil(len(audio) / 1000) + round(24 * line.get("pause_after", 0.2))
    (args.output / "passages.json").write_text(json.dumps(passages, indent=2), encoding="utf8")


if __name__ == "__main__":
    main()
