# pibox ✨🍰✨

[Pi](https://pi.dev) in a box: run the Pi coding agent isolated in a Docker sandbox on your
Mac, with local Ollama models. It only sees the folder you start it in, and it has no
internet access.

> [!WARNING]
> pibox reduces risk by sandboxing Pi, but no sandbox can guarantee security. Review the
> agent's changes, keep backups, and take the precautions your work requires.

## Getting started

Install Ollama and a model:

```bash
brew install ollama
brew services start ollama
ollama pull qwen3.5:9b
```

Install pibox:

```bash
curl -fsSL https://raw.githubusercontent.com/mjme/pibox/main/install.sh | bash
```

Then start it in a project folder:

```bash
cd ~/my-project
pibox
```

Run `pibox --help` for all options and settings, `pibox shell` for a shell in the box, and
`pibox update` to update.

To check the isolation on your own Mac, run `bash tests/isolation-test.sh` from a clone of
this repository.

## How it's isolated

Pi reads and writes files and runs commands, so pibox runs the whole Pi process inside a
Docker container, as Pi's
[containerization guide](https://pi.dev/docs/latest/containerization) describes. On top of
that, pibox adds these restrictions:

- **No internet.** The box sits on a private Docker network with no route out, not even to
  your Mac or your local network. Pi's update checks and telemetry are switched off.
- **Ollama through a filter.** The only thing the box can reach is a small proxy that
  lets Pi chat with the models installed on your Mac. It refuses everything else Ollama
  offers, such as downloading, uploading, creating or deleting models, and it hides
  Ollama cloud models.
- **Only your project folder.** The folder you start pibox in is the only part of your Mac
  the box can see. pibox refuses to start in your home folder, or in places like
  `~/Library` or `~/.ssh`.
- **`.git` is read-only.** The agent can run `git status`, `git diff` and `git log`, but
  can't commit or change git hooks or config, which could otherwise run code on your Mac.
  You review and commit on your Mac. If the agent creates a new git repository, pibox
  warns you when the session ends.
- **A fresh box every time.** The container's system files are read-only, and the
  container is thrown away when you quit Pi. Pi runs as an ordinary user with no special
  privileges.
- **Each project kept apart.** Each folder has its own Pi settings and sessions, so one
  project can't read another's history.

Ollama still runs directly on your Mac, so models get the full speed of your GPU. It only
runs the model, so it never touches your files.

### What pibox can't protect against

- **Code you run yourself.** Anything the agent writes (scripts, tests, a `Makefile`,
  `package.json` scripts, `.envrc`, `.vscode/tasks.json`) runs with your full permissions
  if you run it on your Mac. Review changes first.
- **Secrets in your project.** The agent can read every file in the folder, including
  `.env` files. It can't send them anywhere, but they can end up in its replies and
  sessions.
- **Your terminal.** Pi's output is shown in your terminal. Keep your terminal app up to
  date, and don't let programs write to your clipboard if your terminal offers that
  setting.
- **Docker itself.** All containers share Docker Desktop's Linux VM. A flaw in Docker or
  the Linux kernel could let a container escape into that VM, which can see the folders
  Docker Desktop shares (all of `/Users` by default). Keep Docker Desktop updated, and
  consider limiting **Settings → Resources → File sharing** to the folders you use with
  pibox.

**Recommended:** turn off Ollama's cloud features, so nothing can reach ollama.com through
it. Run `launchctl setenv OLLAMA_NO_CLOUD 1`, then `brew services restart ollama`.

pibox uses plain Docker. [Docker Sandboxes](https://docs.docker.com/ai/sandboxes/) give
each sandbox its own virtual machine, which is a stronger boundary. They don't support
local Ollama models with Pi yet, so pibox doesn't use them.

## Your Pi settings

If you also use Pi directly on your Mac, pibox brings your customizations into the box.

**How it works.** Pi keeps its config in `~/.pi/agent`. That folder also holds your API
keys and past sessions, and Pi's own
[container guide](https://pi.dev/docs/latest/containerization) warns against mounting it
into a container. So pibox doesn't share the whole folder. It shares only the items below,
each **read-only**, at the place Pi looks for them inside the box:

| Shared (read-only) | Never shared |
|---|---|
| `AGENTS.md`, `CLAUDE.md`, `SYSTEM.md`, `APPEND_SYSTEM.md` | `auth.json` (your API keys and logins) |
| `skills/`, `prompts/`, `themes/`, `extensions/` | `sessions/` (can contain secrets from past chats) |
| `keybindings.json` | `models.json`, `mcp.json` (can contain keys) |
| `settings.json` (merged, see below) | `trust.json`, `bin/`, `install/` (Mac-only) |

Pi in the box can use your skills, prompts and instructions, but it can't change them.
Edits you make on your Mac show up the next time you start pibox.

`settings.json` is the one exception, because Pi writes to its own copy. pibox mounts
yours read-only off to the side and copies your settings into the box's copy each time it
starts. Pibox picks the model, so your Mac's default model isn't copied. Settings you
change inside the box stick for that folder, unless your Mac's `settings.json` also sets
them.

To turn sharing off, run `PIBOX_SHARE_CONFIG=0 pibox`. If you've moved Pi's folder with
`PI_CODING_AGENT_DIR`, pibox uses that location instead.

**Project settings.** pibox mounts your project at the same path inside the box, so the
project's own `.pi/` settings, skills and prompts, and its `AGENTS.md`, load just as they
do on your Mac. A few differences:

- Pi asks you to trust each project once inside the box, because your Mac's trust
  decisions aren't shared.
- `AGENTS.md` files in folders *above* your project aren't loaded. The box can only see
  the project folder.
- Extensions, packages and MCP servers that need the internet won't work.

Absolute Mac paths in your settings (like `/Users/you/...`) don't exist in the box.
Extensions that rely on Mac-only npm packages won't load there either.

## License

pibox is released under the [MIT License](LICENSE).
