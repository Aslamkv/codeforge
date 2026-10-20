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

- Python 3.14 or newer (check with `python3 --version`; on a Mac, `brew install python`)
- macOS or Linux (on Windows, use [WSL](https://learn.microsoft.com/windows/wsl/))
- [Ollama](https://ollama.com)

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

Tinker only goes on the web when you ask: `tinker learn`, `tinker research`, or a run with `--web`.

No key? Switch to DuckDuckGo, which needs none:

```sh
tinker use-web duckduckgo
```

Switch back any time with `tinker use-web ollama`.

## First Steps 🚀

Check that everything is ready (it tells you how to fix what is missing):

```sh
tinker doctor
```

Pull a model, then measure it once and use it:

```sh
ollama pull qwen3
tinker calibrate qwen3
tinker use-model qwen3
```

To learn (`tinker learn`, `--web`), Tinker also needs a small embedding model:

```sh
ollama pull qwen3-embedding:0.6b
tinker embed qwen3-embedding:0.6b
```

See the models Ollama has, and which are measured:

```sh
tinker models
```

## Build 🔨

Build something new. It gets its own folder in `~/Projects`, named after the task (`calc-py`), and
Tinker shows you the plan first:

```sh
tinker task a calculator in Python
```

Build the plan, or change it first (it waits again). Name the project with `@` and its folder or ID:

```sh
tinker confirm @calc-py
tinker amend @calc-py also handle negative numbers
```

Give its folder another name before it is built:

```sh
tinker rename @calc-py calculator
```

Build straight through, with no plan to confirm:

```sh
tinker task a todo list app --no-confirm
```

Build from a spec you wrote:

```sh
tinker task a calculator in Python --spec plan.md
```

Look up the web while building:

```sh
tinker task a weather app --web
```

Forgot a project's name? List every project, newest first:

```sh
tinker runs
```

Add a feature, or fix a bug (inside the project's folder, `@calc-py` can be left out):

```sh
tinker add @calc-py a history of past results
tinker fix @calc-py typing 2/0 crashes, it should say "cannot divide by zero"
```

See how a project is going, and what its runs printed (or its model calls):

```sh
tinker status @calc-py
tinker log @calc-py
tinker log @calc-py --calls
```

Go on with a run that stopped, or build a project again from scratch (it asks first):

```sh
tinker resume @calc-py
tinker restart --workdir ~/Projects/calc-py
```

## Talk 💬

Talk with Tinker. Every command works here, and anything else is a normal chat (`exit` ends it):

```sh
tinker
```

Or say one thing at a time:

```sh
tinker chat what makes a good unit test
tinker research the newest Python release
tinker command show the five biggest files here
```

`command` shows the command line first, and runs it only once you agree.

Run a project you built (the last one, by default) with its arguments:

```sh
tinker run add 5 2
tinker run @calc-py add 5 2
```

In the conversation or a chat, give it input after `<<<`: `run @calc-py <<< 2+2`.

Stop the tasks that run (they are saved), or stop them just long enough to answer you:

```sh
tinker stop
tinker emergency how full is my disk
```

## Chat Channels 📡

Talk to Tinker from Telegram, and follow every task there.

1. Make a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Add it. Tinker asks for the token, then asks you to send the bot a message, to pair your chat:

```sh
tinker channel add
```

3. Turn it on, then listen (plain `tinker` in a terminal listens too):

```sh
tinker channel enable telegram
tinker channel start
```

Only your paired chat is listened to. Manage your channels:

```sh
tinker channel list
tinker channel disable telegram
tinker channel remove telegram
```

## Learn 🧠

Teach Tinker a language, a tool, or a whole domain (it reads the web, and keeps what it learns):

```sh
tinker learn rust
tinker learn Flask
tinker learn food bank inventory
```

Teach it a command of yours, which builds may then use:

```sh
tinker learn serve "python3 -m http.server"
```

See what it has learned:

```sh
tinker memory
```

## Settings 🔧

See the settings, or change one:

```sh
tinker config
tinker config test_timeout 90
```

Delete parts of what Tinker keeps, or all of it (your projects stay):

```sh
tinker clear memory runs
tinker reset
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
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest
```
