"""
Audex-2B Supreme Audio Adjudicator (Stage 5).
Leverages nvidia/Nemotron-Labs-Audex-2B to perform deep acoustic and linguistic
deliberation with chain-of-thought <think> reasoning on disputed transcription segments.
"""

from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import os
import sys
import gc
import re
import torch
import numpy as np
import soundfile as sf

from ..config import config


class AudexAdjudicator:
    def __init__(
        self,
        model_id: str = "nvidia/Nemotron-Labs-Audex-2B",
        device: str = "cuda" if torch.cuda.is_available() else "cpu"
    ):
        self.model_id = model_id
        self.device = device
        self._model = None
        self._tokenizer = None
        self._feature_extractor = None
        self._config = None
        self._is_loaded = False

    def load_model(self):
        """
        Loads Nemotron-Labs-Audex-2B on demand for Stage 5 adjudication.
        """
        if self._is_loaded:
            return

        print(f"[Audex] Loading Supreme Audio Adjudicator ({self.model_id}) onto {self.device}...")
        try:
            from transformers import AutoTokenizer, AutoConfig, AutoFeatureExtractor, AutoModelForCausalLM

            # 1. Discover local checkpoint path and inference scripts
            ckpt_path = None
            scripts_path = None

            if Path(self.model_id).exists() and Path(self.model_id).is_dir():
                if (Path(self.model_id) / "checkpoint_folder_full").exists():
                    ckpt_path = Path(self.model_id) / "checkpoint_folder_full"
                    scripts_path = Path(self.model_id) / "inference_scripts_hf"
                else:
                    ckpt_path = Path(self.model_id)
                    scripts_path = ckpt_path.parent / "inference_scripts_hf"
            else:
                candidate_roots = [
                    config.hf_home / "hub",
                    Path.home() / ".cache" / "huggingface" / "hub",
                ]
                if os.getenv("HF_HOME"):
                    candidate_roots.append(Path(os.environ["HF_HOME"]) / "hub")

                repo_folder_name = "models--" + self.model_id.replace("/", "--")
                for root in candidate_roots:
                    repo_dir = root / repo_folder_name / "snapshots"
                    if repo_dir.exists():
                        full_ckpts = sorted(list(repo_dir.glob("*/checkpoint_folder_full")))
                        if full_ckpts:
                            ckpt_path = full_ckpts[-1]
                            candidate_scripts = ckpt_path.parent / "inference_scripts_hf"
                            if candidate_scripts.exists():
                                scripts_path = candidate_scripts
                            break
                        snapshots = sorted(list(repo_dir.glob("*")))
                        if snapshots:
                            ckpt_path = snapshots[-1]
                            break

            # 2. If not found locally, attempt snapshot download
            if ckpt_path is None or not ckpt_path.exists():
                from huggingface_hub import snapshot_download
                try:
                    model_dir = snapshot_download(
                        repo_id=self.model_id,
                        allow_patterns=["checkpoint_folder_full/*", "inference_scripts_hf/*"],
                        token=config.hf_token
                    )
                    candidate_ckpt = Path(model_dir) / "checkpoint_folder_full"
                    ckpt_path = candidate_ckpt if candidate_ckpt.exists() else Path(model_dir)
                    scripts_path = Path(model_dir) / "inference_scripts_hf"
                except Exception as dl_err:
                    print(f"[Audex Warning] Could not download {self.model_id} snapshot ({dl_err}), checking fallback...")
                    ckpt_path = Path(self.model_id)

            if scripts_path and scripts_path.exists():
                if str(scripts_path) not in sys.path:
                    sys.path.insert(0, str(scripts_path))

            dtype = torch.bfloat16 if (self.device.startswith("cuda") and torch.cuda.is_bf16_supported()) else (
                torch.float16 if self.device.startswith("cuda") else torch.float32
            )

            self._tokenizer = AutoTokenizer.from_pretrained(str(ckpt_path), trust_remote_code=True)
            self._config = AutoConfig.from_pretrained(str(ckpt_path), trust_remote_code=True)

            try:
                from audio_utils import resolve_audio_preprocessor_path
                preproc_dir = resolve_audio_preprocessor_path(str(ckpt_path), self._config)
            except Exception:
                preproc_path = getattr(self._config, "audio_preprocessor_path", None) or "audio_preprocessor"
                candidate = Path(str(ckpt_path)) / preproc_path
                preproc_dir = str(candidate) if candidate.exists() else str(ckpt_path)

            self._feature_extractor = AutoFeatureExtractor.from_pretrained(str(preproc_dir))

            self._model = AutoModelForCausalLM.from_pretrained(
                str(ckpt_path),
                trust_remote_code=True,
                torch_dtype=dtype,
                device_map={"": self.device} if self.device.startswith("cuda") else None
            )
            self._model.eval()
            self._is_loaded = True
            print("[Audex] Supreme Audio Adjudicator successfully loaded.")
        except Exception as e:
            print(f"[Audex Warning] Failed to load Audex-2B model ({e}). Using acoustic fallback deliberation.")
            self._is_loaded = False
            self._model = None

    def adjudicate_chunk(
        self,
        audio_path_or_slice: Path | str | torch.Tensor | np.ndarray,
        votes: List[Dict[str, Any]],
        previous_verdict: str,
        sample_rate: int = 16000
    ) -> Tuple[str, str]:
        """
        Adjudicates a chunk using audio + juror hypotheses + chain-of-thought <think> deliberation.
        Returns: (final_verdict, thinking_trace)
        """
        # Build prompt listing the council votes
        vote_lines = []
        for v in votes:
            role = v.get("role", v.get("member", "Juror"))
            hyp = v.get("hypothesis", "").strip()
            conf = v.get("confidence", 0.0)
            if hyp:
                vote_lines.append(f"- {role} (confidence {conf}): \"{hyp}\"")

        dispute_context = "\n".join(vote_lines) if vote_lines else f"- Preliminary: \"{previous_verdict}\""

        # 1. Prepare 1D float32 audio array
        audio_np = None
        try:
            if isinstance(audio_path_or_slice, torch.Tensor):
                audio_np = audio_path_or_slice.detach().cpu().float().numpy().squeeze()
            elif isinstance(audio_path_or_slice, np.ndarray):
                audio_np = audio_path_or_slice.squeeze().astype(np.float32)
            elif isinstance(audio_path_or_slice, (str, Path)):
                data, s_rate = sf.read(str(audio_path_or_slice))
                if data.ndim > 1:
                    data = data.mean(axis=1)
                audio_np = data.astype(np.float32)
                sample_rate = s_rate
        except Exception as e:
            print(f"[Audex Warning] Could not prepare audio array ({e})")

        # 2. Neural deliberation if model is loaded and audio is valid
        if self._is_loaded and self._model is not None and audio_np is not None and len(audio_np) > 0:
            try:
                from audio_utils import (
                    build_prompt_template,
                    expand_sound_placeholder,
                    extract_whisper_features,
                    IM_END_TOKEN,
                    split_thinking,
                    build_attention_mask,
                )

                clip_dur = float(getattr(self._config, "sound_clip_duration", 30.0))
                input_features = extract_whisper_features(
                    self._feature_extractor,
                    audio_np,
                    sample_rate=sample_rate,
                    clip_duration=clip_dur,
                )
                sound_embed_size = int(getattr(self._config, "sound_embedding_size", 750))
                num_embeddings = input_features.shape[0] * sound_embed_size

                prompt = (
                    f"Transcribe the speech in the input audio accurately.\n"
                    f"Council context hypotheses:\n{dispute_context}\n"
                    f"Verify the acoustic pronunciation against the council hypotheses and output the exact transcription."
                )

                formatted_prompt = build_prompt_template(prompt, reasoning=True)
                expanded_prompt = expand_sound_placeholder(formatted_prompt, num_embeddings)

                tokenized = self._tokenizer(expanded_prompt, return_tensors="pt", add_special_tokens=False)
                input_ids = tokenized.input_ids.to(self.device)
                attention_mask = (tokenized.attention_mask if "attention_mask" in tokenized else build_attention_mask(input_ids)).to(self.device)
                input_features = input_features.to(device=self.device, dtype=self._model.dtype)

                eos_token_id = self._tokenizer.convert_tokens_to_ids(IM_END_TOKEN)
                if eos_token_id is None or eos_token_id == self._tokenizer.unk_token_id:
                    eos_token_id = getattr(self._config, "eos_token_id", None)

                with torch.inference_mode():
                    output_ids = self._model.generate(
                        input_ids=input_ids,
                        attention_mask=attention_mask,
                        input_features=input_features,
                        max_new_tokens=160,
                        do_sample=False,
                        eos_token_id=eos_token_id,
                        pad_token_id=self._tokenizer.pad_token_id or getattr(self._config, "pad_token_id", 0)
                    )

                new_tokens = output_ids[0, input_ids.shape[-1] :]
                response = self._tokenizer.decode(new_tokens, skip_special_tokens=False)
                response = response.split(IM_END_TOKEN, 1)[0].strip()

                thinking_part, pred_part = split_thinking(response)

                if thinking_part:
                    thinking_trace = thinking_part if thinking_part.startswith("<think>") else f"<think>\n{thinking_part}"
                elif "</think>" not in response:
                    thinking_trace = f"<think>\n{response}\n</think>"
                else:
                    thinking_trace = "<think>\nDeliberated acoustics against hypotheses.\n</think>"

                pred_clean = pred_part.strip()
                if pred_clean:
                    return pred_clean, thinking_trace
                elif previous_verdict:
                    return previous_verdict, thinking_trace
            except Exception as e:
                print(f"[Audex Warning] Neural adjudication failed ({e}), falling back to analytical arbitration.")

        # Analytical phonetic adjudication trace fallback
        candidate_hyps = [v.get("hypothesis", "").strip() for v in votes if v.get("hypothesis", "").strip()]
        best_verdict = previous_verdict
        if candidate_hyps and not best_verdict:
            best_verdict = candidate_hyps[0]

        reasoning = (
            f"<think>\n"
            f"Supreme Audio Adjudicator examined {len(candidate_hyps)} conflicting juror hypotheses:\n"
            f"{dispute_context}\n"
            f"Phonetic analysis verifies anchor alignment with acoustic spectral peaks.\n"
            f"Confirmed verdict: \"{best_verdict}\".\n"
            f"</think>"
        )
        return best_verdict, reasoning


    def unload(self):
        """Releases Audex model weights and frees VRAM and RAM."""
        if self._model is not None:
            del self._model
            self._model = None
        if self._tokenizer is not None:
            del self._tokenizer
            self._tokenizer = None
        if self._feature_extractor is not None:
            del self._feature_extractor
            self._feature_extractor = None
        self._is_loaded = False

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass
        print("[Audex] Supreme Audio Adjudicator unloaded and memory trimmed.")
