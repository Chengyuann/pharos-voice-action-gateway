"""Offline Whisper worker; invoked only by the explicitly configured local runtime."""
import json
import sys
import wave


def main():
    import numpy as np
    import torch
    from transformers import WhisperProcessor, WhisperForConditionalGeneration

    with wave.open(sys.argv[1], "rb") as wav:
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if float(np.sqrt(np.mean(samples**2))) < 0.003:
        print(json.dumps({"text": ""}))
        return
    processor = WhisperProcessor.from_pretrained(sys.argv[2], local_files_only=True)
    model = WhisperForConditionalGeneration.from_pretrained(sys.argv[2], local_files_only=True)
    inputs = processor(samples, sampling_rate=16000, return_tensors="pt")
    options = {"language": "en", "task": "transcribe"} if getattr(model.generation_config, "is_multilingual", False) else {}
    with torch.inference_mode():
        output = model.generate(inputs.input_features, max_new_tokens=128, **options)
    print(json.dumps({"text": processor.batch_decode(output, skip_special_tokens=True)[0]}))


if __name__ == "__main__":
    main()
