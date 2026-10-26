# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenNoter is an AI video note generation tool. It extracts content from video links (Bilibili, YouTube, Douyin, Kuaishou, local files) and generates structured Markdown notes using LLM models. Full-stack app with a FastAPI backend, React frontend, and optional Tauri desktop packaging.

## Development Commands

### Backend (Python 3.11 + FastAPI)
```bash
cd backend
pip install -r requirements.txt
python main.py                    # Starts on 0.0.0.0:8483
pytest                            # Run tests in backend/tests/
pytest tests/test_request_chunker.py::test_name   # Run a single test
```

### Frontend (React 19 + Vite + TypeScript)
```bash
cd OpenNoter_frontend
npm ci            # 仓库只跟踪 package-lock.json（没有 pnpm-lock.yaml）
npm run dev       # Dev server on port 3015, proxies /api to backend
npm run build     # Production build
npm run lint      # ESLint
npm test          # Vitest（配置独立于 vite.config，见下方说明）
npm run test:watch
```

**前端测试的注意事项**：`vitest.config.ts` **刻意不复用** `vite.config.ts` ——
后者挂了 `@react-spectrum/s2` 的 parcel style 宏，在 node 环境加载不了，
直接 import 会失败。测试跑在 node 环境（不用 jsdom），目前覆盖
`lib/urls.ts` 的纯函数（23 条）。

这也是 `lib/urls.ts` 为什么从 `lib/api.ts` 里拆出来、且保持零依赖：
`api.ts` 会拉进 S2 的宏，导致其中的纯函数无法单测。

### Docker
```bash
docker compose up                              # Web stack (backend + frontend + nginx)
docker compose -f docker-compose.gpu.yml up    # GPU variant
```

### Desktop (Tauri)
```bash
cd backend && ./build.sh          # Build PyInstaller backend binary
cd OpenNoter_frontend && npm ci && npx tauri build
```

### Browser Extension (Vue 3 + vitesse-webext, MV3)
```bash
cd OpenNoter_extension
pnpm install
pnpm dev          # watch mode → ./extension/
pnpm build        # production build → ./extension/
pnpm typecheck
pnpm test         # Vitest unit tests
pnpm test:e2e     # Playwright e2e
```
Load unpacked at `chrome://extensions/` → select `OpenNoter_extension/extension/`. Talks to the same backend at `http://localhost:8483` (configurable in the options page). CORS in `backend/main.py` already accepts `chrome-extension://` and `moz-extension://` via regex.

## Architecture

**Backend** (`backend/`) — FastAPI app, entry point `main.py`:
- `app/routers/` — API routes: `note.py` (generation), `provider.py`, `model.py`, `config.py`, `chat.py` (RAG Q&A on generated notes)
- `app/services/` — Business logic:
  - `note.py` — `NoteGenerator` orchestrates the full pipeline (download → transcribe → LLM → notes)
  - `task_serial_executor.py` — task queue
  - `chat_service.py` + `chat_tools.py` + `vector_store.py` — RAG-based AI Q&A with Function Calling, indexing transcripts and video metadata
  - `cookie_manager.py` — per-platform cookie storage; injected into yt-dlp by downloaders (e.g. Bilibili)
  - `transcriber_config_manager.py` — persisted transcriber settings
- `app/downloaders/` — Platform adapters (bilibili, youtube, douyin, kuaishou, local) with shared `base.py` interface
- `app/transcriber/` — Speech-to-text engines (fast-whisper, groq, bcut, kuaishou, mlx-whisper) with factory in `transcriber_provider.py`. YouTube path prefers existing subtitles and skips audio download when available.
- `app/gpt/` — LLM integration with factory pattern (`gpt_factory.py`), prompt templates (`prompt.py`, `prompt_builder.py`), and `request_chunker.py` for long transcripts
- `app/db/` — SQLite + SQLAlchemy: DAO pattern (`provider_dao.py`, `model_dao.py`, `video_task_dao.py`), models in `models/`
- `app/utils/` — `response.py` (ResponseWrapper), `video_helper.py` (screenshots via FFmpeg), `secret_box.py` (Fernet/HKDF encryption for cookies & provider keys), `static_signing.py` (per-task static-asset tokens), `path_safety.py` / `note_paths.py` / `upload_paths.py` (path sandboxing), `url_safety.py` (`assert_safe_url` + `safe_get` with per-hop redirect validation)
- `app/cli.py` — ops CLI (`python -m app.cli create-admin|promote|list-users`); the only way to bootstrap the first admin when OIDC auto-promotion is disabled
- `app/validators/video_url_validator.py` — URL → platform detection (mirrored client-side in the extension)
- `app/exceptions/` — `BizException` + handlers wired in `main.py` via `register_exception_handlers`
- `backend/events/` — Blinker signal system for post-processing (e.g., temp file cleanup after transcription); registered in `lifespan` startup
- `backend/ffmpeg_helper.py` — `ensure_ffmpeg_or_raise` is called at startup; respects `FFMPEG_BIN_PATH`

**Frontend** (`OpenNoter_frontend/src/`) — React 19 + Vite + **@react-spectrum/s2** (Adobe Spectrum 2):
- `pages/workbench/` — Main note UI: `NoteForm.tsx` (input), `NotePreview.tsx` + `MarkdownArticle.tsx` (preview), `MindmapView.tsx` (mind map via markmap), `VideoBanner.tsx`, `HistoryPanel.tsx`
- `pages/settings/` — LLM provider / transcriber / downloader / API-key / user / monitor settings
- `pages/LoginPage.tsx`, `pages/OnboardingPage.tsx`, `pages/WorkbenchPage.tsx`
- `store/` — Zustand stores: `taskStore`, `authStore`, `modelStore`, `providerStore`, `chatStore`, `themeStore`. `taskStore` persists to IndexedDB via `idb-keyval`.
- `lib/api.ts` — axios instance + response interceptor; desktop auth uses an **in-memory** token sent as `Authorization: Bearer` (web relies on same-origin cookies). `lib/urls.ts` holds the zero-dependency URL helpers (kept separate so they are unit-testable — `api.ts` pulls in `@react-spectrum/s2`'s parcel macros, which fail to load in plain node).
- `services/` — Axios API clients matching backend routes
- `hooks/useTaskPolling.ts` — Polls task status every 3 seconds; also hydrates placeholder tasks that are SUCCESS server-side but have no local body
- UI is **@react-spectrum/s2** (not Tailwind/shadcn). Chinese copy is hard-coded — there is **no i18n layer**.
- Path alias: `@` → `./src`

**Core Workflow**: User submits URL → task queued → download video → extract audio (FFmpeg) → transcribe (Whisper/Groq/etc) → generate notes (LLM) → frontend polls for completion → display Markdown + mind map.

**Browser Extension** (`OpenNoter_extension/`) — Vue 3 + Vite + UnoCSS + webextension-polyfill, MV3:
- `src/popup/Popup.vue` — main entry: detects platform from active tab URL, drives generate flow, shows progress + markdown
- `src/options/Options.vue` — settings: backend URL, default provider/model (loaded from `/get_all_providers` + `/get_models_by_provider/{id}`), quality, screenshot/link toggles, style
- `src/logic/api.ts` — backend API client (uses `settings.backendUrl`, unwraps `ResponseWrapper`, absolutizes `/static/screenshots/...` image paths)
- `src/logic/storage.ts` — `chrome.storage.local`-backed Pinia-like state via `useWebExtensionStorage` for settings + last 30 tasks
- `src/logic/platform.ts` — URL → platform detection mirroring `backend/app/validators/video_url_validator.py`
- `src/sidepanel/`, `src/contentScripts/` — placeholders for P2/P3 (floating button, side panel mind map, RAG chat); not wired into MVP UX
- `src/manifest.ts` — MV3 manifest, popup is default action; `host_permissions: *://*/*`
- Polling lives client-side in popup (3 s interval while open); MV3 service worker is intentionally thin in P1

## Key Configuration

- **Ports**: Backend 8483, Frontend dev 3015, Docker maps 3015→80
- **Environment**: Root `.env` (copy from `.env.example`). LLM API keys are configured through the UI, not env vars.
- **Database**: SQLite at `backend/app/db/open_noter.db`, auto-initialized on first run
- **FFmpeg**: Required system dependency for video/audio processing
- **Vite proxy**: Dev server proxies `/api` and `/static` to backend (configured in `vite.config.ts`, reads env from parent dir; falls back to current dir when `DOCKER_BUILD` is set)
- **CORS**: `backend/main.py` uses a regex (`CORS_ORIGIN_REGEX`) that allows localhost, `tauri.localhost`, and `chrome-extension://` / `moz-extension://` origins — required for the desktop app and the browser extension.

## Code Style

- **Frontend**: ESLint + Prettier (2 spaces, single quotes, 100 char width, Tailwind plugin). TypeScript strict mode.
- **Backend**: Python with type hints. No configured linter. Uses Pydantic models for validation.
- **Note**: The frontend directory is named `OpenNoter_frontend` (not "Bili").
