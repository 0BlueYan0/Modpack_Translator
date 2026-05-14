# Model and Weight Licensing

This file clarifies how licensing applies to the repository assets and to the
external base model used by the application.

## Repository assets

The source code, configuration files, scripts, documentation, and the included
LoRA adapter are licensed under the MIT License. See `LICENSE`.

The included LoRA adapter is:

- `adapter/minecraft_translator_gemma4_e4b_lora.gguf`

Copyright (c) 2026 Koudesuk

This adapter was trained by the repository author and is distributed under the
same MIT License as the project source code.

## Base model

The base GGUF model is not included in this repository. By default, the
application downloads it at runtime through `huggingface_hub` using the values
in `configs/model.yaml`:

- Hugging Face repository: `unsloth/gemma-4-E4B-it-GGUF`
- GGUF filename: `gemma-4-E4B-it-Q4_K_M.gguf`

The upstream model card identifies the base model as Apache-2.0 licensed. The
MIT License in this repository does not relicense the upstream base model.
Users are responsible for complying with the upstream model license and any
updated terms published by the upstream model provider.

## Third-party game assets

This repository does not include Minecraft, Mojang/Microsoft, or third-party
modpack assets. Users are responsible for ensuring they have the right to scan,
modify, translate, and redistribute any modpack files they process with this
tool.
