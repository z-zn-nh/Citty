---
language:
  - zh
  - en
  - ja
  - yue
license: apache-2.0
library_name: transformers
pipeline_tag: automatic-speech-recognition
tags:
  - speech-recognition
  - asr
  - end-to-end
  - multilingual
  - arxiv:2509.12508
---


# Fun-ASR-Nano (Hugging Face Transformers)

This is the Hugging Face Transformers-compatible version of [Fun-ASR-Nano-2512](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512).

Fun-ASR-Nano is an end-to-end speech recognition model by [FunAudioLLM](https://github.com/FunAudioLLM), trained on tens of millions of hours of real speech data. This checkpoint supports Chinese, English, and Japanese; its Chinese coverage includes 7 dialect groups and 26 regional accents. For 31-language recognition, use the separate [Fun-ASR-MLT-Nano-2512](https://huggingface.co/FunAudioLLM/Fun-ASR-MLT-Nano-2512) checkpoint.

## Transformers quickstart

Start with the released **Transformers 5.17.0** package and this native `-hf` checkpoint. A repository clone, custom model code and `trust_remote_code=True` are not required for this path. The original toolkit checkpoint and the vLLM/GGUF artifacts are different formats, not interchangeable model names.

The following setup targets **Linux x86-64, Python 3.12, CPU-only**. Create a new environment rather than upgrading an existing FunASR or vLLM service in place. GPU placement, attention backends and mixed precision require their own validation; CPU is an explicit choice for this example, not a limitation of the model architecture.

```bash
python3.12 -m venv .venv-funasr-native
. .venv-funasr-native/bin/activate
python -m pip install --index-url https://download.pytorch.org/whl/cpu \
  'torch==2.10.0+cpu' 'torchaudio==2.10.0+cpu'
python -m pip install 'transformers==5.17.0' \
  'numpy==1.26.4' 'librosa==0.11.0' 'soundfile==0.13.1'
python -m pip check
```

Matching torch and torchaudio are required: the native feature extractor uses `torchaudio.compliance.kaldi.fbank`. This is a tested environment, not a lock of every transitive dependency. Record your platform and `python -m pip freeze` when reproducing results.

The model revision below fixes the official weights, tokenizer and chat template. The first run downloads them unless cached. The public English audio URL also uses a fixed revision of the original model repository, not an invented `-hf` example directory.

```python
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

torch.set_num_threads(4)
model_id = "FunAudioLLM/Fun-ASR-Nano-2512-hf"
revision = "d93b302ee7fd505e1b3576120fc142fc6f7820e1"
processor = AutoProcessor.from_pretrained(
    model_id, revision=revision, trust_remote_code=False, token=False
)
model = AutoModelForSpeechSeq2Seq.from_pretrained(
    model_id, revision=revision, trust_remote_code=False, token=False,
    dtype=torch.float32,
).to("cpu").eval()

audio_url = (
    "https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512/resolve/"
    "272c57b82523ada6fd87095e955f8e29100979ab/example/en.mp3"
)
inputs = processor.apply_transcription_request(
    audio=audio_url,
    language="en",
    processor_kwargs={
        "return_tensors": "pt",
        "audio_kwargs": {"sampling_rate": 16000},
    },
)

with torch.inference_mode():
    generated_ids = model.generate(**inputs, max_new_tokens=128, do_sample=False)
new_tokens = generated_ids[:, inputs.input_ids.shape[1]:]
print(processor.batch_decode(new_tokens, skip_special_tokens=True)[0])
```

Expected transcription for this official short example in the verified environment:

```text
The tribal chieftain called for the boy, and presented him with fifty pieces of gold.
```

Remove the full input tensor width before decoding: generated IDs include the prompt. The 128-token cap bounds this short example; it can truncate longer outputs. URL loading resamples to the processor's 16 kHz input rate. For local arrays, explicitly resample and choose a mono-channel policy before labeling their sampling rate.

For domain-specific transcription, pass contextual information with `prompt` and hotwords with `keywords`; the checkpoint chat template builds the complete instruction. The following prepares another request using the same processor and audio URL:

```python
inputs = processor.apply_transcription_request(
    audio=audio_url,
    language="en",
    prompt="A tribal story involving a chieftain and a boy.",
    keywords=["tribal chieftain", "fifty pieces of gold"],
    processor_kwargs={
        "return_tensors": "pt",
        "audio_kwargs": {"sampling_rate": 16000},
    },
)
```

Hotwords are hints, not an enforced vocabulary. In the paired Chinese sample check, the candidate keyword did not force the requested spelling into the output. Evaluate names and domain terms on representative recordings instead of assuming that accepted prompt fields improve accuracy.

`language` accepts Chinese, English, and Japanese as ISO codes, full English names, or the checkpoint Chinese names (`中文`, `英文`, `日文`). Keep batch inputs and decoded outputs in order; use `processor_kwargs={"text_kwargs": {"padding": True}}` together with the other processor options for different-length batches. Reject empty batches before calling the processor. An upstream `IndexError` for `audio=[]` was observed in the historical `fc501` source environment; that observation is not a new 5.17.0 test result.

## Using the pipeline API

Use the explicitly selected `any-to-any` task with structured audio/language
messages: the [runnable pipeline example](https://github.com/QwenAudio/Fun-ASR/tree/main/examples/transformers#use-the-transformers-pipeline-api)
was executed on Transformers 5.17.0, torch 2.10.0+cpu and the pinned public English
sample. It returns transcription text without the input conversation.

The standard `automatic-speech-recognition` pipeline is a different processing
route: our 5.17.0 test fails with a floating-point token-index error for this
checkpoint. Do not substitute that task or rely on automatic task inference.
This pipeline check is CPU single-file only, not GPU/batch, accuracy or capacity
validation. The native processor/generate quickstart above remains supported.

## CUDA with the canonical CLI

The [isolated CUDA recipe](https://github.com/QwenAudio/Fun-ASR/tree/main/examples/transformers#cuda-an-isolated-tested-recipe)
provides separate GPU requirements and explicit `--device cuda --dtype bfloat16`
selection. Keep the CPU environment above separate. CUDA requests fail when
unavailable rather than silently falling back; BF16 requires device support.

On 2026-09-10, Linux/Python 3.12, H100 80 GB, driver 550.127.08,
Transformers 5.17.0 and matching torch/torchaudio 2.11.0+cu128 passed eight
float32/BF16 English, Chinese, keyword and padded Chinese/English batch cases
using the pinned public snapshot. Exact CLI GPU single-file/batch and default
CPU regression also passed. The Chinese keyword error remains; these are not
accuracy, throughput, minimum-VRAM or concurrency benchmarks. Other GPUs,
float16 and hosted GPU Space/Colab execution were not validated.

An attention-dispatch warning was observed. Functional success does not verify
every component's attention kernel or imply Flash Attention performance.

## Capability boundary

This Transformers checkpoint is for generation-based transcription. It does not include the native CTC branch, so this export does not provide CTC-dependent timestamps or speaker diarization. Native model support does not add an HTTP server, realtime audio transport, or known-speaker identification.

Choose the artifact and runtime together: use the [original checkpoint](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512) for the FunASR toolkit, or the separate [Fun-ASR-Nano-2512-vllm checkpoint](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512-vllm) with its [native vLLM guide](https://github.com/modelscope/FunASR/blob/main/docs/vllm_official_native_validation.md). Check the selected pipeline's timestamp and speaker capabilities separately; changing a repository suffix does not convert weights or add those capabilities.

## Verification and further reading

- [Canonical runnable Transformers example](https://github.com/QwenAudio/Fun-ASR/tree/main/examples/transformers).
- [Companion notebook](https://github.com/QwenAudio/Fun-ASR/blob/main/examples/colab/fun_asr_nano_transformers.ipynb).

The 5.17.0 stable-wheel checks used the pinned official snapshot, CPU float32, torch 2.10.0+cpu and four Torch threads. The official English URL, a resampled Chinese waveform, a Chinese keyword request and a padded Chinese/English batch returned non-empty text and EOS before the 128-token limit. These checks cover only two short public samples and are not a CER/WER, keyword-benefit, memory-capacity, concurrency, Space-health or GPU benchmark. Cached official weights were rechecked: SHA256 `1bbb6dcc5d8b75084a399d48c4d4b0f3aa1d3f09f2616ae40ed2c4fba03d89c9`.

Historical reproducibility: native support was merged in [huggingface/transformers#46180](https://github.com/huggingface/transformers/pull/46180) at `fc501343edfccdc840eb8594a6cafa8185c2de53`. Earlier checks used a build from that source before 5.17.0 was released. The source archive is no longer the primary installation path.

- [Native Transformers guide in English](https://www.funasr.com/en/docs/native-transformers.html) and [Chinese](https://www.funasr.com/docs/native-transformers.html), including local audio, batches and exact verification boundaries.
- [Why checkpoint format matters in an application](https://www.funasr.com/en/blog/fun-asr-nano-transformers.html).
- [Merged Transformers implementation and model documentation](https://github.com/huggingface/transformers/blob/fc501343edfccdc840eb8594a6cafa8185c2de53/docs/source/en/model_doc/fun_asr_nano.md), for the native API and further implementation details.
- [Original model card](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512), for the model family and toolkit-specific usage.
