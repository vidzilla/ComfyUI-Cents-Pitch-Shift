import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import torch


class CentsPitchShiftR3:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO",),
            "cents": ("FLOAT", {"default": 0.0, "min": -2400.0,
                                  "max": 2400.0, "step": 0.01}),
            "preserve_formants": ("BOOLEAN", {"default": False}),
            "rubberband_path": ("STRING", {"default": "rubberband-r3"}),
        }}

    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("audio",)
    FUNCTION = "shift"
    CATEGORY = "audio/effects"

    def shift(self, audio, cents, preserve_formants, rubberband_path):
        cents = float(cents)
        if not math.isfinite(cents):
            raise ValueError("Cents must be finite.")
        waveform = audio["waveform"]
        sr = int(audio["sample_rate"])
        if waveform.ndim != 3 or min(waveform.shape) < 1 or sr <= 0:
            raise ValueError("Expected non-empty AUDIO [batch, channels, samples] and positive sample rate.")
        if not waveform.is_floating_point() or not torch.isfinite(waveform).all().item():
            raise ValueError("Audio must contain finite floating-point samples.")
        if cents == 0:
            return (audio,)

        import numpy as np
        import soundfile as sf

        requested = os.path.expandvars(os.path.expanduser(rubberband_path.strip()))
        executable = shutil.which(requested)
        if executable is None and Path(requested).is_file():
            executable = str(Path(requested).resolve())
        if executable is None:
            raise RuntimeError("Install Rubber Band CLI 3.0+ or set rubberband_path to its executable (rubberband.exe on Windows). Installing a Python package alone is not enough.")

        original_length = waveform.shape[-1]
        results = []
        with tempfile.TemporaryDirectory(prefix="comfy_cents_") as folder:
            source = str(Path(folder) / "input.wav")
            target = str(Path(folder) / "output.wav")
            for batch in waveform.detach().to(device="cpu", dtype=torch.float32):
                sf.write(source, batch.transpose(0, 1).numpy(), sr, subtype="FLOAT")
                Path(target).unlink(missing_ok=True)
                command = [executable, "-q", "-3", "-t", "1.0", "-p", format(cents / 100.0, ".12g")]
                if preserve_formants:
                    command.append("-F")
                command.extend([source, target])
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
                try:
                    process = subprocess.run(command, capture_output=True, text=True,
                                             errors="replace", **options)
                except OSError as exc:
                    raise RuntimeError(f"Cannot launch Rubber Band: {exc}") from exc
                if process.returncode:
                    message = (process.stderr or process.stdout).strip()
                    raise RuntimeError(f"Rubber Band failed. This node requires version 3.0+ with R3 support.\n{message[-4000:]}")
                shifted, output_sr = sf.read(target, dtype="float32", always_2d=True)
                if output_sr != sr or shifted.shape[1] != batch.shape[0]:
                    raise RuntimeError("Rubber Band returned an unexpected sample rate or channel count.")
                if not len(shifted) or not np.isfinite(shifted).all():
                    raise RuntimeError("Rubber Band returned empty or invalid audio.")
                if len(shifted) < original_length:
                    shifted = np.pad(shifted, ((0, original_length - len(shifted)), (0, 0)))
                shifted = np.ascontiguousarray(shifted[:original_length].T)
                results.append(torch.from_numpy(shifted))

        output = dict(audio)
        output["waveform"] = torch.stack(results).to(device=waveform.device, dtype=waveform.dtype)
        return (output,)


NODE_CLASS_MAPPINGS = {"CentsPitchShiftR3": CentsPitchShiftR3}
NODE_DISPLAY_NAME_MAPPINGS = {"CentsPitchShiftR3": "Cents Pitch Shift (Rubber Band R3)"}
