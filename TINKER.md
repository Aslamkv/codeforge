# Tinker 🔧

Tinker is how CodeForge builds software today.

> 🌱 **This will change.** Today Tinker builds with AI, because that is the best tool of our time.
> Tomorrow the best tool may be something else, and Tinker will change with it. Our values will not.

Tell Tinker what you want. Tinker plans it, builds it, tests it, and learns something new every time.

- 🧠 **Plans first.** Every detail is thought through before any work starts.
- 🧱 **Builds small.** Simple pieces that work well together.
- ✅ **Tests the goal.** Checks the result the way a person would use it.
- 📒 **Remembers.** Each run makes the next one faster.
- 🔁 **Any model.** Switch the AI whenever you like.
- 💬 **One conversation.** The terminal and a chat app are the same Tinker.

## What You Need 🧰

- Python 3
- [Ollama](https://ollama.com), with at least one model pulled

## Install 📦

```sh
git clone https://github.com/Aslamkv/codeforge
cd codeforge
./install.sh
```

This links `tinker` into `~/.local/bin`, so you can run it from any folder.
To update later, just `git pull`. The link always points at the latest code.

## Web Search 🌐

We don't reinvent wheels. When something good already exists, we use it.

So Tinker uses Ollama's own **web search** and **web fetch** by default. It needs a free key:

1. Make a free account at [ollama.com](https://ollama.com).
2. Create a key at [ollama.com/settings/keys](https://ollama.com/settings/keys).
3. Put it in your shell profile (`~/.zshrc` or `~/.bashrc`):

```sh
export OLLAMA_API_KEY="your-key-here"
```

Tinker only goes on the web when you ask: `tinker learn`, or a run with `--web`.

No key? Switch to DuckDuckGo, which needs none:

```sh
tinker use-web duckduckgo
```

Switch back any time with `tinker use-web ollama`.

## First Steps 🚀

Check that everything is ready:

```sh
tinker doctor
```

Pick a model and measure it:

```sh
tinker models
tinker calibrate qwen3
tinker use-model qwen3
```

## Usage ✨

Talk with Tinker:

```sh
tinker
```

Build something new (it gets its own folder in `~/Projects`). Tinker shows you the plan first:

```sh
tinker task a calculator in Python
```

Build the plan, or change it first (it waits again):

```sh
tinker confirm
tinker amend also handle negative numbers
```

Build straight through, with no plan to confirm:

```sh
tinker task a todo list app --no-confirm
```

Name a project from anywhere with `@` and its folder or ID:

```sh
tinker status @calc-py
tinker log @calc-py
```

Add a feature, or fix a bug:

```sh
tinker add a history of past results
tinker fix typing 2/0 crashes, it should say "cannot divide by zero"
```

Look up the web while building:

```sh
tinker task a weather app --web
```

See how things are going:

```sh
tinker status
tinker runs
```

Teach Tinker something new:

```sh
tinker learn rust
```

Get help:

```sh
tinker help
tinker help task
```

## Safety 🛡️

Tinker runs the code it writes: setup commands, tests and the app itself, as you, on your computer.
It never writes outside the project folder, but the code it writes can do anything you can.

- 🧪 Want it walled off? Run Tinker as a separate user, or in a VM or container.
- 🌐 With `--web`, pages can steer the model. Read the plan before you `confirm`.
- 💬 A paired chat can run commands on your computer. Pair only your own private chat, never a group.
  Keep the bot token secret.

## Specification 📜

[SPEC.md](SPEC.md) says exactly what Tinker is and does, one numbered point at a time.
The tests in `tests/` enforce every point. Run them from the repository root:

```sh
python -m pytest
```
