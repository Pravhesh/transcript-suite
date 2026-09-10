"""
Route A (Default): NeMo TitaNet Speaker Diarizer.
Runs 100% locally with open weights; no HuggingFace token required.
"""

from typing import List
import tempfile
from pathlib import Path
import torch
import numpy as np
import soundfile as sf
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from .base import BaseDiarizer, SpeakerTurn
from ..audio.vad import SileroVADSegmenter


class NeMoTitaNetDiarizer(BaseDiarizer):
    def __init__(self, model_name: str = "titanet_large", device: str = "cuda" if torch.cuda.is_available() else "cpu"):
        self.model_name = model_name
        self.device = device
        self.model = None
        self._is_loaded = False
        self.vad = SileroVADSegmenter(max_chunk_duration=6.0, min_chunk_duration=0.8)

    def _load_model(self):
        if self._is_loaded:
            return
        print(f"[NeMo Diarizer] Loading TitaNet ({self.model_name}) on {self.device}...")
        try:
            import nemo.collections.asr as nemo_asr
            self.model = nemo_asr.models.EncDecSpeakerLabelModel.from_pretrained(model_name=self.model_name)
            if self.device.startswith("cuda") and torch.cuda.is_available():
                self.model = self.model.to(self.device)
            self.model.eval()
            self._is_loaded = True
            print("[NeMo Diarizer] TitaNet model successfully loaded.")
        except Exception as e:
            print(f"[NeMo Diarizer Warning] Could not load NeMo TitaNet: {e}.")
            self._is_loaded = False

    def unload_model(self):
        """
        Unloads TitaNet model and trims memory.
        """
        if self.model is not None:
            del self.model
            self.model = None
        self._is_loaded = False
        import gc
        import ctypes
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass
        print("[NeMo Diarizer] TitaNet model unloaded and RAM trimmed.")

    def diarize(self, waveform: torch.Tensor, sample_rate: int = 16000) -> List[SpeakerTurn]:
        """
        Extracts speaker embeddings on VAD speech slices and clusters them into speaker IDs.
        """
        duration = waveform.shape[1] / sample_rate
        # 1. Get speech intervals using fine-grained VAD
        segments = self.vad.segment(waveform, duration)
        if not segments:
            return [SpeakerTurn(start=0.0, end=duration, speaker="Speaker 0")]

        # If only 1 segment, it's just Speaker 0
        if len(segments) == 1:
            return [SpeakerTurn(start=segments[0].start, end=segments[0].end, speaker="Speaker 0")]

        try:
            self._load_model()
        except Exception:
            pass

        if not self._is_loaded or self.model is None:
            # Fallback: assign uniform speaker
            return [SpeakerTurn(start=s.start, end=s.end, speaker="Speaker 0") for s in segments]

        embeddings = []
        valid_segments = []

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            for idx, s in enumerate(segments):
                start_sample = int(s.start * sample_rate)
                end_sample = int(s.end * sample_rate)
                slice_wav = waveform[:, start_sample:end_sample].squeeze(0).cpu().numpy()
                
                slice_file = tmppath / f"seg_{idx}.wav"
                sf.write(str(slice_file), slice_wav, sample_rate, subtype="PCM_16")

                with torch.inference_mode():
                    emb = self.model.get_embedding(str(slice_file))
                    if isinstance(emb, torch.Tensor):
                        emb = emb.squeeze().cpu().numpy()
                    embeddings.append(emb)
                    valid_segments.append(s)

                slice_file.unlink(missing_ok=True)

        if not embeddings:
            return [SpeakerTurn(start=s.start, end=s.end, speaker="Speaker 0") for s in segments]

        # 2. Cluster speaker embeddings with cosine distance
        emb_matrix = np.array(embeddings)
        # Normalize embeddings
        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        emb_matrix = emb_matrix / norms

        if len(valid_segments) > 1:
            distances = pdist(emb_matrix, metric="cosine")
            distances = np.nan_to_num(distances, nan=1.0)
            # Threshold ~0.65 separates different speakers effectively
            linkage_matrix = linkage(distances, method="average")
            cluster_ids = fcluster(linkage_matrix, t=0.65, criterion="distance")
            # Convert clusters to 0-indexed labels: Speaker 0, Speaker 1, ...
            unique_clusters = {cid: f"Speaker {i}" for i, cid in enumerate(sorted(set(cluster_ids)))}
            speaker_labels = [unique_clusters[cid] for cid in cluster_ids]
        else:
            speaker_labels = ["Speaker 0"]

        # 3. Create merged speaker turns
        speaker_turns = []
        for s, spk in zip(valid_segments, speaker_labels):
            speaker_turns.append(SpeakerTurn(start=s.start, end=s.end, speaker=spk))

        return self._merge_consecutive_turns(speaker_turns)

    def _merge_consecutive_turns(self, turns: List[SpeakerTurn]) -> List[SpeakerTurn]:
        """
        Merges adjacent segments belonging to the same speaker if the gap is small (< 1.0s).
        """
        if not turns:
            return []

        merged = [turns[0]]
        for curr in turns[1:]:
            prev = merged[-1]
            if prev.speaker == curr.speaker and (curr.start - prev.end) < 1.0:
                prev.end = curr.end
            else:
                merged.append(curr)
        return merged
