Requirement:--
1. fully desktop app

Tech stack:--
1. React (SPA), Tauri (desktop shell), Go (control plane, vendor services, workers, license API in the ideal layout), Python only for data ETL/ML in the isolated data-processor—not on the HTTP hot path.
2. gPRC, ConnectGRPC, http2, Protobuf (via Buf) for high-speed
3. Icons: Lucide only (lucide-react in the SPA). UI components: Mantine
4. Secrets: HashiCorp Vault
5. db: sqlite

DIrectory:--
1. Separation of concerns: Strict boundaries between code, environment, config, secrets, and runtime data (volumes, Postgres, caches)—no secret keys in images; config is not secrets.
2. Repository hygiene: Clean directory tree, clear split of concerns, no huge source files—prefer small, simple, organized units so developers can change behavior safely.


Standards/ policy:--
1. Modularity and control surface: Highly modular, config-driven, feature switches (server-side kill switches), centralized manifest-driven behavior, single source of truth for what the product can do, feature flags, and server-driven UI (SDUI)—aligned with §6.
2. Engineering discipline: Standards and policy govern how code is written and reviewed—repo .cursor/rules/, docs/adr/ for hard decisions, AGENTS.md, CI checks, and coding standards stay authoritative.
3. Authority (non‑negotiable): This Base requirement block is the source of truth for stack and control/data plane boundaries. If §4, §5, §6, §8, §10, or linked plans (MVP1.1 / MVP1.2, MVP2) say something different—e.g. Python for platform-api, platform-worker, or license-api—treat that as out of date until edited. Python is allowed only in data-processor (ETL/ML). Any exception needs an explicit docs/adr/ and owner approval.


env:--
1. Use Pixi only for toolchain (repo root `pixi install` → `.pixi/`).
2. pin each exact version in pixi.toml


Package/ distribution:--
1. i want to send user the app package bundle installer  so user can install and use the app.
2. I want smallest installer - what's bundled vs downloaded  - to provide "stub" installer. The installer starts, checks the OS, and then downloads the specific "heavy" binaries. 
3. I want to use Distribution Frameworks - Windows: Inno Setup, macOS: Packages or DMG with "First-Run" Logic, Linux:  Flatpak



License :-- 
1. license check - without license actiavtion - issue - renew - cancel :--


End-user experience:--
1. Easy to install the package and start using the product (happy path via installer + hooks; IT/advanced paths documented).