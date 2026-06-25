---
title: docforge
emoji: 📄
colorFrom: yellow
colorTo: green
sdk: docker
app_port: 8000
pinned: false
license: mit
---

# docforge

Multi-agent docs generator — point at a repo, get an honest `docs/` folder back.
Every claim grounded in `[file:line]` and verified by a critic loop.

This Space runs the FastAPI app from the [docforge repo](https://github.com/).
The explainer site and the real `/example` run work with **no API key**; set
`GROQ_API_KEY` as a Space secret to enable live "paste a GitHub URL" runs.

> This file is the **Hugging Face Space** README — its YAML front matter tells
> HF to build the Dockerfile and route traffic to port 8000. Copy it to the root
> of your Space repo (it replaces the Space's own README, not the project README).
