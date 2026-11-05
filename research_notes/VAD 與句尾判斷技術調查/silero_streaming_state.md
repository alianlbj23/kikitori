# Silero VAD streaming mode: chunk size, context, recurrent state and reset policy (official sources only)

Research date: 2026-11-05. Sources are limited to the snakers4/silero-vad repo (code, README, wiki, release notes, maintainer replies in issues and discussions) and the pipecat-ai/pipecat repo (code, PRs, commits).

Permalink anchors used below:
- silero-vad `master` at commit `066409f3d9db45a30d21dfcbf9147826ffdd7052` (2026-10-22). Call it **M**: `https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py`
- silero-vad tag `v6.2.3` = `5cd7945676eb32225748052e2e6a0580e4686a08` (2026-09-23, latest release). Call it **T623**.
- silero-vad tag `v5.1.2` = `6478567951ae5c9979ad7b234185b5515f4be7a1`. Call it **T512**.
- pipecat tag `v1.14.0` = `1f8f79e357bd8d5632ac2d7ef7a8199441836a85` (released 2026-10-24). Call it **P1140**.

Labels: **[DOC]** = explicitly documented (README, wiki, release notes, docstrings). **[SRC]** = evident from source code. **[MAINT]** = maintainer statement in an issue or discussion. **[INF]** = my inference.

## Q1. Required chunk size per call; other sizes; what happens with a wrong size

### Takeaway
Since v5, each model call must get exactly 512 samples at 16 kHz or 256 samples at 8 kHz (32 ms). The official Python wrappers raise `ValueError` for any other size. Calling the raw ONNX model directly does not enforce this: sizes up to 639 samples may run without error, larger sizes crash, and the maintainers say other sizes are "not recommended ... quality may suffer". Before v5 (v3/v4), 256–1536-sample windows were allowed.

### Cited Findings
- [DOC] v5.0 release notes: "`window_size_samples` is deprecated - now the VAD only works with fixed size window"; "VAD now works with 8 kHz and 16 kHz sample rates, only with fixed 256 and 512 sample windows respectively"; "Quality difference for different window sizes is negligible => window size was deprecated"; "Sample rates that are a multiple of 16 kHz are still supported". — [v5.0 release](https://github.com/snakers4/silero-vad/releases/tag/v5.0)
- [SRC] `OnnxWrapper.__call__` sets `num_samples = 512 if sr == 16000 else 256` and raises `ValueError(f"Provided number of samples is {x.shape[-1]} (Supported values: 256 for 8000 sample rate, 512 for 16000)")` if the last dimension differs. This is lines 112–115 in M and T623 and lines 55–58 in T512. — [M L109-L115](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L109-L115); [T512 L52-L58](https://github.com/snakers4/silero-vad/blob/6478567951ae5c9979ad7b234185b5515f4be7a1/src/silero_vad/utils_vad.py#L52-L58)
- [SRC] `_validate_input` first strides multiples of 16 kHz down to 16 kHz (`x = x[:,::step]`). It then raises `"Supported sampling rates: ... (or multiply of 16000)"` for any other rate, and `"Input audio chunk is too short"` when `sr / x.shape[1] > 31.25`, i.e. the chunk is shorter than 32 ms. — [M L85-L101](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L85-L101)
- [SRC] `get_speech_timestamps` keeps a `window_size_samples` argument, documented as "!!! DEPRECATED, DOES NOTHING !!!". It recomputes `window_size_samples = 512 if sampling_rate == 16000 else 256` and zero-pads the final short chunk. — [M L294, L350-L351, L381, L388-L392](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L381-L392)
- [DOC] The wiki's "Stream imitation example" uses `window_size_samples = 512 if SAMPLING_RATE == 16000 else 256` and **breaks** (drops) a final chunk shorter than the window. "Entire audio inference" says: "currently only chunks of size 512 are used for 16 kHz and 256 for 8 kHz ... 0.032 s = 32.0 ms". — [Wiki: Examples-and-Dependencies](https://github.com/snakers4/silero-vad/wiki/Examples-and-Dependencies)
- [MAINT] adamnsandle (collaborator), 2026-01-29, on why the raw ONNX model accepts up to 639 samples: the input is right-padded by 64 samples, then an STFT (hop 128, window 256) runs, so 640 samples give exactly 4 frames. The encoder downsamples by 4 into 1 state, and more than 640 produces 2+ states and a decoder shape error. "Ultimately, it is not recommended to use values other than 512 (for 16 kHz) or 256 (for 8 kHz), quality may suffer." — [issue #750](https://github.com/snakers4/silero-vad/issues/750#issuecomment-3818116832)
- [MAINT] snakers4 (owner) on an 8 kHz problem report first asked: "When working with 8 kHz natively, do you use the right window size?" (quoting `window_size_samples = 512 if sampling_rate == 16000 else 256`). — [issue #765](https://github.com/snakers4/silero-vad/issues/765#issuecomment-4036852920)
- [SRC/DOC] Historical: v3.0 had "Flexible chunk size, minimum chunk size is just 30 milliseconds!" and its example used `window_size_samples = 1536`. v4.0 `get_speech_timestamps` warned on sizes other than `[512, 1024, 1536]` (16k) or `[256, 512, 768]` (8k). — [v3.0 release](https://github.com/snakers4/silero-vad/releases/tag/v3.0); [v4.0 utils_vad.py L235-L238](https://github.com/snakers4/silero-vad/blob/v4.0/utils_vad.py#L235-L238)
- [DOC] v6.2.2 added a separate offline `silero_vad_16k_sequence.onnx` with `get_speech_timestamps_sequence` (16 kHz only). It is for offline, whole-file inference, not streaming. — [v6.2.2 release](https://github.com/snakers4/silero-vad/releases/tag/v6.2.2)

### Inferences
- [INF] Streaming code that receives arbitrary transport frames (for example 20 ms / 320 samples from WebRTC) must re-buffer into exact 512/256-sample windows. Do not pad or truncate each transport frame.
- [INF] Since the raw ONNX graph silently accepts 512–639 samples, a non-Python port can feed wrong sizes without any error. The only safeguard is wrapper-side validation like `OnnxWrapper`'s.

### Gaps
- No official quality numbers exist for non-standard chunk sizes in v5+. The maintainers only say "quality may suffer".

## Q2. Context samples prepended from the previous chunk (v5+)

### Takeaway
Yes. Since v5, every call prepends the last 64 samples (16 kHz) or 32 samples (8 kHz) of the previous model input, so the ONNX input is 576 or 288 samples. The context starts as zeros, is updated after every call, and is cleared by `reset_states()`. The JIT/PyTorch model does this internally. ONNX users must do it themselves. Without it, probabilities collapse to about 0.001.

### Cited Findings
- [DOC] v5.0 release notes: "Slightly changed internal logic, now some context (part of previous chunk) is passed along with the current chunk". — [v5.0 release](https://github.com/snakers4/silero-vad/releases/tag/v5.0)
- [SRC] `OnnxWrapper.__call__` does the following:
  - `context_size = 64 if sr == 16000 else 32` (L118).
  - On first use, `self._context = torch.zeros(batch_size, context_size)` (L127–128).
  - `x = torch.cat([self._context, x], dim=1)` (L130).
  - After inference, `self._context = x[..., -context_size:]` (L139), i.e. the last 64/32 samples of the concatenated input, which equal the tail of the current chunk.
  - Same logic in T512, L61/L71/L82.
  
  — [M L117-L141](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L117-L141); [T512 L61-L82](https://github.com/snakers4/silero-vad/blob/6478567951ae5c9979ad7b234185b5515f4be7a1/src/silero_vad/utils_vad.py#L61-L82)
- [SRC] `reset_states()` sets `self._context = torch.zeros(0)`, so the next call re-initialises the context to zeros. — [M L103-L107](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L103-L107)
- [SRC] The official C++ ONNX example mirrors this: `context_samples; // 64 for 16kHz, 32 for 8kHz`, `_context` holds "the last 64/32 samples from the previous chunk (initialized to zero)", and "effective_window_size = window_size_samples + context_samples; // e.g., 512 + 64 = 576 samples". It copies the last `context_samples` of `new_data` into `_context` after every call. — [examples/cpp/silero-vad-onnx.cpp L89-L95, L169-L172, L220-L221, L419-L427](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/examples/cpp/silero-vad-onnx.cpp#L89-L95)
- [DOC] The C++ example context was fixed in v6.0 ("Fix/cpp vad context", PR #605). — [v6.0 release](https://github.com/snakers4/silero-vad/releases/tag/v6.0)
- [MAINT] Issue #771 (2026-04-27): using `silero_vad.onnx` directly with raw `[1, 512]` frames "produces near-zero speech probabilities (~0.001) for every frame". The reporter traced this to the missing 64-sample context. snakers4 replied: "This plagued us some time after the release of the new version. The issue is that the ONNX-model is actually two models with an if inside. Hence it has a dynamic shape. As for the docs - there is no obvious place in the README, and most likely people will keep missing it." Later: "it is not possible to raise a warming within ONNX itself." — [issue #771](https://github.com/snakers4/silero-vad/issues/771#issuecomment-4333159230); [follow-up](https://github.com/snakers4/silero-vad/issues/771#issuecomment-4474475349)
- [MAINT] snakers4 on the PyTorch path: "For PyTorch we tucked the changes inside of the model". — [discussion #726](https://github.com/snakers4/silero-vad/discussions/726)
- [SRC] pipecat's own `SileroOnnxModel` re-implements the same context logic in NumPy (`context_size = 64 if sr == 16000 else 32`, concat, keep last `context_size`). — [pipecat P1140 silero.py L116-L141](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L116-L141)

### Inferences
- [INF] Context and recurrent state are two separate pieces of carried memory. A "reset" in both the Silero wrapper and pipecat clears **both**: the next window sees 64 zero samples instead of the real preceding audio, and a zeroed LSTM state.

### Gaps
- No official documentation in README prose explains the context requirement. The maintainer explicitly acknowledged this gap (#771).

## Q3. Recurrent state shape; what `reset_states()` does; when to reset; when VADIterator calls it

### Takeaway
In v5/v6 the ONNX state is a single tensor `state` of shape `(2, batch, 128)` float32. v4 instead had separate `h` and `c` tensors of shape `(2, batch, 64)`. `reset_states()` zeroes that state and clears the context. The wrappers also reset automatically when batch size or sample rate changes.

Official usage resets once per audio stream or file: at session start and/or end, or "each time the audio stream changes or ends". `VADIterator` never resets inside `__call__`; it resets only in `__init__` and when the user calls `reset_states()`. In one 2026 answer the owner also said "Ideally yes" to resetting after each `speech_end`. The maintainers have never prescribed a periodic or timed reset.

### Cited Findings
- [SRC] `OnnxWrapper.reset_states(batch_size=1)` sets `_state = torch.zeros((2, batch_size, 128))`, `_context = torch.zeros(0)`, `_last_sr = 0` and `_last_batch_size = 0`. — [M L103-L107](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L103-L107)
- [SRC] ONNX I/O in v5+: inputs `{'input', 'state', 'sr'}`, outputs `(out, state)`. The new state is written back every call (`self._state = torch.from_numpy(state)`). — [M L131-L135](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L131-L135)
- [SRC] v4: `self._h = np.zeros((2, batch_size, 64))`, `self._c = np.zeros((2, batch_size, 64))`, inputs `'h'` and `'c'`. — [v4.0 utils_vad.py L45-L47, L64-L66](https://github.com/snakers4/silero-vad/blob/v4.0/utils_vad.py#L45-L66)
- [SRC] Automatic resets inside `__call__` happen only on the first call (`not self._last_batch_size`), on a sample-rate change, or on a batch-size change. — [M L120-L125](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L120-L125)
- [SRC] `VADIterator.reset_states()` calls `self.model.reset_states()` and sets `triggered = False`, `temp_end = 0` and `current_sample = 0`. It is called in `__init__` (L637). `__call__` (L647–689) never calls it, including on `'start'` or `'end'` events. — [M L637-L689](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L637-L689); same in [T623 L632-L682](https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/utils_vad.py#L632-L682)
- [SRC] The VADIterator docstring calls it a "Class for stream imitation". — [M L601-L602](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L601-L602)
- [SRC] `get_speech_timestamps` calls `model.reset_states()` exactly once, before the loop over the whole audio (L383). `audio_forward` also resets once at its start (L149). The C++ example's `process()` likewise resets once per input file. — [M L383](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L383); [M L149](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L149); [cpp L334-L337](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/examples/cpp/silero-vad-onnx.cpp#L334-L337)
- [DOC] The wiki streaming examples end with `vad_iterator.reset_states() # reset model states after each audio` and `model.reset_states() # reset model states after each audio`. — [Wiki: Examples-and-Dependencies](https://github.com/snakers4/silero-vad/wiki/Examples-and-Dependencies)
- [DOC] Wiki FAQ: "Do models keep state, should chunks be sent sequentially? Yes. Though the models were designed for streaming, they can also be used to process long audios. ... the `jit` model for example has method `model.reset_states()`." — [Wiki: FAQ](https://github.com/snakers4/silero-vad/wiki/FAQ)
- [MAINT] snakers4, 2024-11-07 (accepted answer): "Yes, it does, the VAD considers previous chunks." and "The state should be reset each time the audio stream changes or ends." — [discussion #572](https://github.com/snakers4/silero-vad/discussions/572)
- [MAINT] snakers4, 2025-11-18 (accepted answer to "How often reset_states() should be called in never-ending streaming scenarios"): "`reset_states()` should be called when the new audio stream starts and / or the old one ends (e.g. at the begging of each new session)." — [discussion #726](https://github.com/snakers4/silero-vad/discussions/726) (converted from [issue #725](https://github.com/snakers4/silero-vad/issues/725))
- [MAINT] snakers4, 2026-07-31 (accepted answer). To "should I call reset_states() after each speech_end? If I don't reset, will it affect subsequent detection?" he replied: "Ideally yes. Otherwise it will "think" that this is one long audio." In a follow-up: "It should not degrade if you resent states". The asker later said their degradation "was my own code issue". — [discussion #791](https://github.com/snakers4/silero-vad/discussions/791#discussioncomment-17852352)
- [MAINT] snakers4, 2022-07-18, on why the streaming example doesn't reset after each chunk: he pointed to the VADIterator example, which resets "after each audio". — [discussion #206](https://github.com/snakers4/silero-vad/discussions/206) (converted from [issue #205](https://github.com/snakers4/silero-vad/issues/205))
- [MAINT] snakers4, 2023-03-15, to a user who got high false positives: "there is a proper iterator class in the utils as an example, your code for example does not reset model state on start." — [issue #309](https://github.com/snakers4/silero-vad/issues/309#issuecomment-1469251451)

### Inferences
- [INF] Official guidance is not fully consistent. The code and wiki show reset per stream or file only. One maintainer answer (#791) says reset after each `speech_end` is "ideally" correct. None suggests resetting on a timer during silence.
- [INF] Resetting after `speech_end` is a different policy from pipecat's. It happens once per detected utterance, right after the end event. Pipecat's reset is periodic (every ~2 s) for as long as the stream is quiet.
- [INF] A reset also zeroes the 64-sample context. So the window right after a reset is always computed with a discontinuity: real audio preceded by zeros.

### Gaps
- No official explanation of what the recurrent state encodes or how quickly it "forgets".
- No official statement on how many windows the model needs to warm up after a reset.

## Q4. Is there official guidance against periodic resets during a continuous stream? What does pipecat say and why?

### Takeaway
No Silero maintainer statement explicitly forbids or endorses periodic resets. Their only guidance is reset per stream or session (plus the one "Ideally yes" after `speech_end`). The maintainers have also confirmed at least one long-stream file where the model "breaks" without resets, and said they "cannot understand why".

Pipecat's justification for resetting changed over time:
- Up to v1.12.0: "memory will keep growing otherwise", with a 5 s wall-clock reset regardless of speech.
- PR #6202 (2026-10-16): removed resets on the v6.2 model, saying resets cause premature stops mid-word.
- PR #6207 (same day): reintroduced a reset every 2 s of audio, only during clear non-speech. The stated reason is that the carried state drifts after music or long silence/noise (strongly at 8 kHz) and under-reports following speech.

This 2 s gated reset first shipped in pipecat v1.13.0 and is what v1.14.0 contains. Pipecat itself reports a cost: about 1% of utterances detected 200–400 ms late, when a reset lands just before speech. No official source reports whole utterances being missed because of the reset.

### Cited Findings
**Silero side**
- [MAINT] In #726, the reporter (progmars) saw 16 kHz streaming confidences collapse after ~30 s without resets. Resetting every chunk was "overkill and hurts detection". Resetting every 10 s "seemed to work the best" (user experiment, not maintainer guidance). snakers4 replied:
  - "I confirm, that the VAD breaks on this file, we need to take a closer look" (2025-11-18).
  - "We cannot understand why this happens On other long test files the VAD works fine The only hack that kind of works is resampling to 8 kHz" (2025-11-19).
  - "Please share your audio as well it will be helpful with testing a newer release" (2026-10-29).
  
  The maintainers never endorsed a reset interval. — [discussion #726](https://github.com/snakers4/silero-vad/discussions/726)
- [Non-maintainer, official repo] In the same discussion (2026-10-29), a third-party user (mariano54, ISSEN) reported the following on v6.2 at 16 kHz with state and context carried for a whole WebRTC call over -40 dBFS noise:
  - About 2.5 min in, speech scored 0.0–0.4 for 30+ s. A freshly reset model scored 0.9+ on the same audio.
  - Two model instances fed 2 min of identical noise "never converge (L2 distance ... around 40-50)".
  - `reset_states()` "restores detection immediately, with no noticeable warm-up".
  - They adopted a pipecat-like gated reset. Its one cost: "a reset landing right before speech started delayed detection by ~190 ms".
  
  This is user evidence, not a maintainer statement. — [discussion #726](https://github.com/snakers4/silero-vad/discussions/726)
- [Non-maintainer, official repo] Issue #765 (open). A user (jjaroztegi, 2026-10-16) reproduced 8 kHz degradation from state carried through silence, using the repo's own `aepyx_8k.wav`. With 7 s silence gaps at 8 kHz: streamed mean p 0.88 (min 0.26) vs fresh state 0.92 (min 0.57), and 4/25 utterances detected >250 ms late. At 16 kHz: 1/25 late. With -60 dBFS noise, both rates had late detections (8k 5/25, 16k 4/25). The user asked whether `reset_states()` after non-speech is "the intended usage". No maintainer answer had been posted as of this research. — [issue #765](https://github.com/snakers4/silero-vad/issues/765)
- [MAINT] snakers4 in #791: "It should not degrade if you resent states", in the context of resetting between segments. — [discussion #791](https://github.com/snakers4/silero-vad/discussions/791)

**pipecat side**
- [SRC] pipecat v1.0.0 through v1.12.0 had `_MODEL_RESET_STATES_TIME = 5.0` with the comment "We need to reset the model from time to time because it doesn't really need all the data and memory will keep growing otherwise." It reset on wall-clock time regardless of VAD state. — [pipecat v1.0.0 silero.py L22-L23, L214-L220](https://github.com/pipecat-ai/pipecat/blob/v1.0.0/src/pipecat/audio/vad/silero.py#L212-L220); [v1.12.0 silero.py L23, L215-L218](https://github.com/pipecat-ai/pipecat/blob/v1.12.0/src/pipecat/audio/vad/silero.py#L215-L218)
- [Pipecat contributor] Issue #2815 "Why is VAD state being reset?" asked "Silero VAD is LSTM based ... Shouldn't the memory used be constant?". filipi87 (GitHub association: CONTRIBUTOR) replied on 2026-04-02 that "ONNX Runtime can accumulate memory in its execution context over long-running sessions" and that the 5 s reset "is a pragmatic workaround", then closed it "as working as intended". — [pipecat issue #2815](https://github.com/pipecat-ai/pipecat/issues/2815#issuecomment-4177861058)
- [Pipecat PR] #6202 "Update Silero VAD to v6.2 and keep its state for the whole stream" (markbackman, merged 2026-10-16) found three things:
  - Mid-utterance resets caused premature stops: "about 60% of the reset positions that fell inside the utterance produced a stop before it ended".
  - "v6 ... drifts through long silence or noise until it stops detecting speech. After 75s of room tone, its confidence on the following speech fell from 0.93 to 0.46". The PR asserted "v6.2 holds its state steady, so the stream no longer needs resets, which is also how Silero's streaming iterator and LiveKit's plugin run it."
  - The commit message says "A reset makes the model briefly lose confidence, and one that lands during speech can end the user's speech early, mid-word."
  
  — [pipecat PR #6202](https://github.com/pipecat-ai/pipecat/pull/6202); [commit 1430096](https://github.com/pipecat-ai/pipecat/commit/1430096092d98396510a73c50a6095ab88285bf2)
- [Pipecat PR] #6207 "Reset Silero VAD's state during clear non-speech" (markbackman, merged 2026-10-16) found that, even with v6.2:
  - "Some non-speech leaves that state unable to detect the speech that follows: music at either sample rate, and long silence or noise at 8 kHz."
  - 8 kHz after a minute of room tone: ~5% of the next utterance detected without resets vs ~85% with resets.
  - Music: missed speech "for 20 s or more", recovering "within about one interval" with resets.
  - Cost: "about 1% of utterances were detected 200–400 ms late in testing. The interval and thresholds come from a small audio set, so real phone calls are the thing to watch."
  
  — [pipecat PR #6207](https://github.com/pipecat-ai/pipecat/pull/6207); [commit 31f4205](https://github.com/pipecat-ai/pipecat/commit/31f4205796807dc38df2b53ec52e0590f07b6239)
- [SRC] pipecat v1.14.0 `silero.py` contains:
  - Module comment: "Non-speech shifts that state so the model under-reports the speech that follows: music strongly at either sample rate, and long silence or noise strongly at 8kHz and slightly at 16kHz."
  - `_MODEL_RESET_INTERVAL_SECS = 2.0`, plus the comment "A shorter interval recovers sooner but puts more resets just ahead of speech, where the model occasionally confirms speech a few hundred milliseconds late."
  - `_MODEL_RESET_MAX_CONFIDENCE = 0.1` and `_MODEL_RESET_LOW_CONFIDENCE_WINDOWS = 5` (~160 ms).
  
  `_maybe_reset_model()` resets once ≥2 s of audio has been scored since the last reset, `_vad_state == VADState.QUIET`, and the last 5 windows are <0.1. The reset calls `self._model.reset_states()`, which clears both `_state` and `_context`. The class docstring adds: "A reset during speech makes the model briefly lose confidence". — [P1140 silero.py L21-L39](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L21-L39); [L95-L104](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L95-L104); [L146-L158](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L146-L158); [L245-L278](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L245-L278)
- [SRC] The same 2 s gated logic is already in pipecat v1.13.0, and `silero.py` is identical between v1.14.0 and `main` as of 2026-11-05. — [pipecat v1.13.0 silero.py L32-L39](https://github.com/pipecat-ai/pipecat/blob/v1.13.0/src/pipecat/audio/vad/silero.py#L32-L39)

### Inferences
- [INF] The "memory growth" rationale (pipecat ≤v1.12, issue #2815) is not supported by Silero's code. The state is a fixed `(2,1,128)` tensor and the context is a fixed 64 samples, so neither grows. Pipecat itself dropped that rationale in v1.13: the current code comments cite state drift, not memory.
- [INF] Pipecat's gate (QUIET plus 5 windows <0.1) is computed **after** scoring a window, and the reset takes effect from the next window. If an utterance starts with a soft or low-probability onset (<0.1 for 160 ms), a reset can land at the very start of speech. The next windows then run with zero LSTM state and 64 zero context samples. Pipecat measured this as "200–400 ms late" in ~1% of cases. Our experiment's finding (whole utterances missed, lower probability for 1–2 s after a reset) is a stronger effect than anything pipecat or Silero report officially. It conflicts with the ISSEN user's report of "no noticeable warm-up" after a reset. The difference might come from sample rate (8 vs 16 kHz), audio domain, or VAD params (`start_secs`, confidence), but I found no official source that resolves it.
- [INF] Official evidence points both ways. Never resetting can make the model go "deaf" after long noise, music, or (at 8 kHz) silence: #726, #765, pipecat #6207, all on v6.x. Resetting periodically can hurt onsets. The Silero maintainers have not chosen between these. Their documented contract is still reset per stream or session.

### Gaps
- No Silero maintainer reply yet to the explicit question in #765: "is `reset_states()` after some time of non-speech the intended usage (and if so, after how long)".
- No official explanation of the long-stream drift; maintainers said "We cannot understand why this happens".
- No official or pipecat-published data on missed **whole** utterances caused by the gated reset.

## Q5. Official recommended parameters for streaming and supported sample rates

### Takeaway
Defaults in both `get_speech_timestamps` and `VADIterator`:
- `threshold = 0.5` ("lazy" 0.5 is "pretty good for most datasets"; tune per dataset).
- `neg_threshold = max(threshold − 0.15, 0.01)`.
- `min_silence_duration_ms = 100`.
- `speech_pad_ms = 30`.
- `min_speech_duration_ms = 250` (only in `get_speech_timestamps`; `VADIterator` has none).

Supported sample rates are 8 kHz and 16 kHz, with integer multiples of 16 kHz decimated to 16 kHz. Not every parameter is configurable on `VADIterator` in the latest release (see Inferences).

### Cited Findings
- [SRC/DOC] `get_speech_timestamps` signature: `threshold=0.5, sampling_rate=16000, min_speech_duration_ms=250, max_speech_duration_s=inf, min_silence_duration_ms=100, speech_pad_ms=30, ..., neg_threshold=None, window_size_samples=512, min_silence_at_max_speech=98, use_max_poss_sil_at_max_speech=True`. Docstring excerpts:
  - "It is better to tune this parameter for each dataset separately, but "lazy" 0.5 is pretty good for most datasets."
  - "Final speech chunks shorter min_speech_duration_ms are thrown out."
  - "In the end of each speech chunk wait for min_silence_duration_ms before separating it."
  - "Final speech chunks are padded by speech_pad_ms each side."
  - "neg_threshold ... If model's current state is SPEECH, values BELOW this value are considered as NON-SPEECH."
  
  — [M L281-L357](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L281-L357)
- [SRC] Floor: `neg_threshold = max(threshold - 0.15, 0.01)` in `get_speech_timestamps_from_probs`. — [M L475-L476](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L475-L476)
- [SRC] VADIterator by version:
  - In **v6.2.3 (latest tag)**, the signature is `threshold=0.5, sampling_rate=16000, min_silence_duration_ms=100, speech_pad_ms=30`, and the exit condition is hard-coded `speech_prob < self.threshold - 0.15`.
  - On **master** (after the v6.2.3 tag, PR #805 merged 2026-09-29), `neg_threshold: float = None` was added, defaulting to `max(threshold - 0.15, 0.01)`.
  - In both, VADIterator only accepts `[8000, 16000]` and has no `min_speech_duration_ms` or `max_speech_duration_s`.
  
  — [T623 L591-L682](https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/utils_vad.py#L591-L682); [M L591-L689](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L591-L689); [commit 0d6b589 "floor VADIterator's negative threshold at 0.01"](https://github.com/snakers4/silero-vad/commit/0d6b589d9dbefcf2e1eabdebd8a21fa23e36b8e1)
- [SRC] VADIterator hysteresis:
  - `'start'` when `speech_prob >= threshold` and not triggered. Start = `current_sample - speech_pad_samples - window_size_samples`.
  - On `speech_prob < neg_threshold` while triggered, it sets `temp_end` and emits `'end'` once `current_sample - temp_end >= min_silence_samples`. End = `temp_end + speech_pad_samples - window_size_samples`.
  - A probability ≥ threshold clears `temp_end`.
  
  — [M L670-L689](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L670-L689)
- [DOC] v5.1.1 release: "add neg_threshold parameter explicitly" (PR #528), for `get_speech_timestamps`. v6.0 release: "fx negative ths bug" (PR #581). — [v5.1.1 release](https://github.com/snakers4/silero-vad/releases/tag/v5.1.1); [v6.0 release](https://github.com/snakers4/silero-vad/releases/tag/v6.0)
- [DOC] Wiki FAQ on tuning: "for majority of use cases no tuning is necessary by design, a good start would be to plot probabilities, select the `threshold`, `min_speech_duration_ms` and `min_silence_duration_ms`." — [Wiki: FAQ](https://github.com/snakers4/silero-vad/wiki/FAQ)
- [MAINT] snakers4's debugging advice: plot the probability chart (`visualize_probs`), isolate the problematic audio, and check "if the problem is systemic, or just improper hyper-paramers". — [issue #515](https://github.com/snakers4/silero-vad/issues/515#issuecomment-2285510908)
- [DOC] Sample rates:
  - README: "supports **8000 Hz** and **16000 Hz**".
  - FAQ: "multiples of `16000` (e.g. `32000` or `48000`) are cast to `16000` inside of the JIT model". The ONNX wrapper decimates with `x[:, ::step]`.
  - FAQ: "8 kHz is standard phone quality, while 16 kHz is usually used for ASR".
  - Model files with `16k` in the name support only 16 kHz.
  
  — [README](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/README.md); [Wiki: FAQ](https://github.com/snakers4/silero-vad/wiki/FAQ); [M L79-L83, L91-L94](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L79-L94)
- [MAINT] On native 8 kHz vs resampling to 16 kHz: "In theory native should work better, but there were some reports than for some cases 16k works better. Better to test on your domain in any case." — [discussion #791](https://github.com/snakers4/silero-vad/discussions/791)
- [DOC] v5.0 release: "Quality difference for 8 kHz and 16 kHz is negligible now". — [v5.0 release](https://github.com/snakers4/silero-vad/releases/tag/v5.0)
- [SRC] pipecat's own wrapper accepts only 8000 or 16000 and does **not** decimate multiples of 16 kHz, even though its error string mentions them. — [P1140 silero.py L79-L93, L200-L214](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L79-L93)
- [SRC] Input scaling: the pipecat wrapper divides int16 by 32768. A Silero collaborator recommended `sound *= 1/32768` instead of per-chunk max normalization, which caused false positives. — [P1140 L234-L236](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L234-L236); [issue #309](https://github.com/snakers4/silero-vad/issues/309#issuecomment-1486942666)

### Inferences
- [INF] With pip `silero-vad==6.2.3`, a custom `neg_threshold` in streaming requires either master or your own iterator. The default exit threshold is 0.35 when threshold = 0.5.
- [INF] Silero gives no separate "streaming-recommended" values. The defaults are the same for offline and streaming, and the guidance is to tune per domain by plotting probabilities.

### Gaps
- No official values for `min_speech_duration_ms` in streaming, because VADIterator has no such parameter. No official streaming guidance on `max_speech_duration_s` either.

## Q6. Official statements on music, singing, noise, languages and known limitations

### Takeaway
Silero claims training on 6000+ languages and robustness to noise. The maintainers openly list as known weaknesses: music with voice-like instruments, singing, and very high-pitched, child or cartoon voices. Music was absent from training data, per a 2024 maintainer statement. Very loud noise and noisy call-center audio with background speech are also weak spots. v6.2 claims improvements on several of these edge cases.

### Cited Findings
- [DOC] README: "trained on huge corpora that include over **6000** languages and it performs well on audios from different domains with various background noise and quality levels". v5.0 release: "The VAD supports more than 6,000 languages now; Significanly more robust on noisy data". — [README](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/README.md); [v5.0 release](https://github.com/snakers4/silero-vad/releases/tag/v5.0)
- [DOC] v6.0 release: "16% less errors on noisy real-life data; 11% less errors on multi-domain validation"; "Known persisting issues: music with human voice-like instruments, very high pitched voices (artificial, cartoons, small children)". — [v6.0 release](https://github.com/snakers4/silero-vad/releases/tag/v6.0)
- [DOC] v6.2 release (2025-11-06): "VAD training paradigm reworked"; "Higher stability on OOD / rare / strange / unique data"; "Significant quality improvements on ... Unusual voices, Child voices, Cartoon voices, Muted voices, Muted speech, Lower quality phone calls" ("no metrics update"). — [v6.2 release](https://github.com/snakers4/silero-vad/releases/tag/v6.2)
- [MAINT] snakers4, 2024-11-04: "It is a known problem with songs / very high voices / children's voices / cartoon voices. As for music per se - we did not have music in the training data." — [issue #565](https://github.com/snakers4/silero-vad/issues/565#issuecomment-2453774166)
- [MAINT] snakers4, 2025-08-26: "issue still persists with v6 with child-like voices and voice-like music instruments" (piano and guitar false positives). — [issue #663](https://github.com/snakers4/silero-vad/issues/663#issuecomment-3222924213)
- [MAINT] snakers4, 2024-08-13 (v5 on noisy call-center audio): v5 "was not tuned on call-like data domain ... the model thinks that this speech is background speech most likely". "The same problem also applies to singing, music, murmur, background TV noises, parrot speech, etc". Users may choose `v3.1`, `v4.0` or latest. — [issue #515](https://github.com/snakers4/silero-vad/issues/515#issuecomment-2285510908)
- [MAINT] snakers4, 2023-09-12: "we viewed VAD as speech / noised speech separation from everything else (silence, mild noise, music)"; "We observed that for very loud noise our VAD behaves not very well." He later said v5 "was designed with this issue in mind and performance on noise-only data was significantly improved". — [issue #369](https://github.com/snakers4/silero-vad/issues/369#issuecomment-1714999536); [2024-06-27](https://github.com/snakers4/silero-vad/issues/369#issuecomment-2195445703)
- [Non-maintainer, official repo] Issue #765 (open): high-pitched female voices are detected worse at native 8 kHz than when resampled to 16 kHz, confirmed by another user on 6.2.1. The maintainer asked for samples; no resolution yet. — [issue #765](https://github.com/snakers4/silero-vad/issues/765)
- [DOC] The wiki Quality-Metrics page includes noise-only datasets (ESC-50, private noisy calls without speech). It notes WebRTC VAD is "pretty poor at separating speech from noise". — [Wiki: Quality-Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)
- [DOC] Wiki FAQ: "The model most likely will have problems with extremely long chunks." — [Wiki: FAQ](https://github.com/snakers4/silero-vad/wiki/FAQ)

### Inferences
- [INF] For media containing BGM or songs, both false positives (instruments) and false negatives (singing, high voices) are documented. Pipecat additionally reports that music *before* speech can suppress detection of the following speech through carried state (#6207).

### Gaps
- No official per-language accuracy breakdown. No official metrics for music or singing.

## Q7. License of code and model weights

### Takeaway
MIT for the repository, "Copyright (c) 2020-present Silero Team". The model weights (`.jit`, `.onnx`, `.safetensors`) are shipped inside the same MIT-licensed repo and pip package, with no separate weights license. The README badge's alt text oddly says "CC BY-NC 4.0" while the badge displays MIT. Pipecat is BSD 2-Clause.

### Cited Findings
- [DOC] `LICENSE`: "MIT License — Copyright (c) 2020-present Silero Team". GitHub API `spdx_id: MIT`. — [LICENSE](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/LICENSE)
- [DOC] README: "Published under permissive license (MIT) Silero VAD has zero strings attached - no telemetry, no keys, no registration, no built-in expiration, no keys or vendor lock." The badge markdown is `[![License: CC BY-NC 4.0](...License-MIT...)]`: the alt text says CC BY-NC 4.0, but the badge text and link point to MIT. — [README L1, L135-L137](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/README.md)
- [DOC] Weights are in-repo under `src/silero_vad/data/` (e.g. `silero_vad_16k.safetensors`, `silero_vad_op18_ifless.onnx`, linked from the README and the v6.2 changelog). — [README L198-L200](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/README.md); [changelog comment](https://github.com/snakers4/silero-vad/issues/2#issuecomment-3637260694)
- [SRC] pipecat `silero.py` header: "SPDX-License-Identifier: BSD 2-Clause License". It bundles its own copy of `silero_vad.onnx`, updated to v6.2 in PR #6202. — [P1140 L1-L5](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L1-L5); [PR #6202 files](https://github.com/pipecat-ai/pipecat/commit/1430096092d98396510a73c50a6095ab88285bf2)

### Inferences
- [INF] The "CC BY-NC 4.0" alt text is most likely a leftover from an older license era. The authoritative LICENSE file is MIT and covers the weights distributed in the repo.

### Gaps
- No standalone license statement specifically naming the weight files. Coverage is implied by their inclusion in the MIT-licensed repo.

## Version-change summary (v4 → v5 → v6.x), relevant to streaming

### Takeaway
v5 was the breaking change for streaming. It moved to fixed 512/256 windows, prepended 64/32 context samples, and replaced `h`/`c` with a single `state` tensor `(2,B,128)`. v6.0 and v6.2 changed training and quality but kept the same streaming I/O contract. Later v6.2.x releases changed packaging and added an offline sequence model. On master, after v6.2.3, VADIterator gained `neg_threshold`.

### Cited Findings
- v4.0: `h` and `c` `(2,B,64)`; windows 256–1536; "Batching is now supported by the ONNX model". — [v4.0 release](https://github.com/snakers4/silero-vad/releases/tag/v4.0); [v4.0 utils_vad.py L45-L66](https://github.com/snakers4/silero-vad/blob/v4.0/utils_vad.py#L45-L66)
- v5.0: fixed windows, context, opset 16, model 2 MB, 6000+ languages. — [v5.0 release](https://github.com/snakers4/silero-vad/releases/tag/v5.0)
- v5.1.1: "VadIterator first chunk bag fx" (PR #505); "add neg_threshold parameter explicitly" (PR #528). — [v5.1.1 release](https://github.com/snakers4/silero-vad/releases/tag/v5.1.1)
- v6.0: new model; "v5 features and improvements kept"; C++ context fix (PR #605); `neg_threshold` floor `max(threshold-0.15, 0.01)` in `get_speech_timestamps`. — [v6.0 release](https://github.com/snakers4/silero-vad/releases/tag/v6.0)
- v6.2: training reworked; edge-case improvements. A wiki entry dated 2025-12-10 adds the "ifless ONNX and tinygrad 16k models". — [v6.2 release](https://github.com/snakers4/silero-vad/releases/tag/v6.2); [Wiki: Version history](https://github.com/snakers4/silero-vad/wiki/Version-history-and-Available-Models)
- v6.2.1: onnxruntime optional. v6.2.2: offline 16k sequence ONNX. v6.2.3: torchaudio optional. — [releases](https://github.com/snakers4/silero-vad/releases)
- Master after v6.2.3: VADIterator `neg_threshold` (PR #805), 2026-09-29. — [commit 1e261b0](https://github.com/snakers4/silero-vad/commit/1e261b036686cd0017d500ee96acd1c4ba572a9d)

### Inferences
- [INF] Pipecat PR #6202 claims v6 drifts during long silence while v6.2 "holds its state steady". This is pipecat's measurement, not a Silero release-note claim. Silero's v6.2 notes say only "Higher stability on OOD / rare / strange / unique data". Later reports (#726 ISSEN comment, #765, pipecat #6207) show drift still occurs on v6.2 in some conditions.

### Gaps
- Silero release notes do not mention state drift or long-stream behaviour in any version.
