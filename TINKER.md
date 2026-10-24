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
- 🗣️ **Speaks plainly.** Every command says what it does in plain words, the same in the terminal and
  in a chat. The technical details (tokens, timings, settings tried) go to the log.

## What You Need 🧰

- Python 3.15 or newer (check with `python3 --version`; on a Mac, `brew install python`)
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

Tinker only goes on the web when you ask: `tinker learn`, `tinker research`, a run with `--web`, or `tinker read`
of a link (that one needs no key: it only downloads the file).

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
Tinker builds it straight through:

```sh
tinker task a calculator in Python
```

It says each step as it goes, and how long it took (the ⏳ line is redrawn in place while the model works):

```text
📝 Plan: the spec (project)
   📋 Language: Python; 3 criteria; files: calc.py, test_calc.py, README.md
   ✅ Spec written (42s)

🔁 Verify (until every test passes)
   🎯 C2 (2/3 pass): calc.sub(5, 3) is 2
      ⏳ thinking… 1m 4s
   ✅ C2 passes (3/3 pass)
```

See the plan first (in the conversation, `tinker` alone, it always waits). Build it, or change it
first (it waits again). Name the project with `@` and its folder or ID:

```sh
tinker task a calculator in Python --confirm
tinker confirm @calc-py
tinker amend @calc-py also handle negative numbers
```

Give its folder another name before it is built:

```sh
tinker rename @calc-py calculator
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

See how a project is going, and what its runs said (or its model calls and the details):

```sh
tinker status @calc-py
tinker log @calc-py
tinker log @calc-py --calls
```

Go on with a project's run that stopped, or build a project again from scratch (it asks first):

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

While a job runs, just ask: a chat or a command line pauses it (it saves itself), you are answered at once, and
the job goes on as soon as you have your answer (see [Jobs](#jobs-)).

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

Type `/` in the chat to pick any command from the bot's menu. With or without the `/`, it works the same:

```text
/status
runs
/use_model qwen3:8b
```

Attach a book, an article or a document to the chat, and Tinker reads it: see [Read](#read-).

The chat stays tidy. Tinker's status is one message: each new status replaces the one before, at the
bottom of the chat, even after a restart. Each task has
one message. Quick looks like `jobs` or `help` go after two minutes, and your question goes as soon as
Tinker answers. Everything else goes after a day. Nothing is lost: Tinker keeps a history of what it showed.

```sh
history          # what the chat showed, newest first, with what you asked
history 2        # the next page
history #12      # show #12 again
clear chat       # empty the chat now (the history stays)
clear history    # forget the history too (asks first)
```

`tinker history` shows the same list in a terminal. For privacy, `clear history` deletes everything Tinker
kept of the chat. Your projects, runs and jobs stay.

## Learn 🧠

Teach Tinker a language, a tool, or a whole domain (it reads the web, and keeps what it learns):

```sh
tinker learn rust
tinker learn Flask
tinker learn food bank inventory
```

A language or a tool is learned mainly from its own documentation, for the version you have installed
(a language's tools too: its test runner, formatter, linter). The first time takes one web search to find
it; a search follows only for what the documentation leaves unanswered. The cleaned pages are kept per
version, so learning it again fetches nothing. When the web cannot help (offline, rate limited, nothing
found), it stops at once and says why, before the model does any work, and keeps what it knew before.
Every web request is logged in `.tinker/web-requests.log`.

Forget the kept documentation (it is fetched again on the next learn):

```sh
tinker clear docs
```

Teach it a command of yours, which builds may then use:

```sh
tinker learn serve "python3 -m http.server"
```

See what it has learned:

```sh
tinker memory
```

See it as a graph you can explore in the browser: each lesson, fact and fix linked to its language, and to
the runs that taught it, proved it or found it wrong (what did not hold is kept, greyed, with why). In a
chat, `memory --graph` sends the page as a file to open on your phone:

```sh
tinker memory --graph
```

Ask what it has learned. It answers just what you ask, from what it keeps, and changes nothing:

```sh
tinker ask what is your current toolchain for python
tinker ask which lessons do you have about tests
```

Correct it when you know better, as many things at once as you like. Mark your own choices with the word
**prefer** ("I prefer no mypy"): Tinker honors them as they are. Everything else is a fact, checked first: Tinker runs commands on your machine that
only show what is installed, and reads web pages about them. Each change is made once the facts it rests
on hold: what you showed wrong is retired (kept, with why), the toolchain is corrected, and your lesson is
kept, with its sources. A change whose facts do not hold is not made, and Tinker says why. It needs the
web (`tinker use-web`):

```sh
tinker teach use ruff and not black for python, ruff already does what black does
tinker teach I prefer no mypy, our spec tests already catch what matters
```

In zsh, a `?` in a command is a wildcard, so leave it out or quote the question.

### Read 📚

Give Tinker a book, an article or a document, and it learns what it teaches. Its whole text is stored first,
numbered on your reading list (#1, #2 ...), so you never send it twice. Then Tinker reads every chapter, the core
ones first: the contents, the foreword and the terms the index mentions most tell it which those are. Each chapter
is saved as soon as it is read, so what it learned is there to ask about while it goes on.

```sh
tinker read ~/Books/clean-architecture.epub          # a file: EPUB, Markdown or plain text
tinker read https://example.org/files/my-book.epub   # a link that downloads it
tinker read list                                     # the reading list: how much of each is read
tinker read show #1                                  # one reading: each chapter, read or not, and what it taught
tinker read #1                                       # go on with the chapters not read yet
tinker read #1 "the chapter on retries: when to give up"   # look again, with that in mind
tinker clear read #1                                 # forget it: its text and what was learned from it
```

In a chat, just attach the file: Tinker stores it and starts reading. Telegram lets a bot download files of up to
20 MB, so send a link for a bigger one. A share link (Google Drive, Dropbox, iCloud) must be its download link
(Dropbox's ends in `?dl=1`).

A PDF is refused: convert it to EPUB first, which keeps its chapters. Calibre does it:

```sh
ebook-convert book.pdf book.epub
```

Reading is a [job](#jobs-) with a job number of its own (`tinker jobs` shows it, apart from its reading number):
it gives way to you like any job, and `tinker stop #J` stops it, everything read so far kept. `tinker resume #J`
(or `tinker read #N`) goes on with the chapters not read yet, without downloading anything again.

Ask about what it read, and correct it when it got something wrong. A correction is checked against the text
itself, and the corrected fact is kept in place of the wrong one:

```sh
tinker ask what does Clean Architecture say about the dependency rule
tinker teach Clean Architecture says entities never depend on use cases, not the other way round
```

What teaches how to build software also guides the builds that need it, like anything else Tinker learned: its
lessons are shown to a build when the request needs them, and kept or retired by how the build goes. Anything else
is there for asking only.

### Exercise 🏋️

Let Tinker practise a language on its own, to write better code faster. It builds small practice projects
one after another in `~/Projects/tinker-exercise`: each one is built, then given a feature. It measures each
project (time, model calls, retried replies, fixes, what failed) and keeps lessons from its mistakes and slow
steps. The next projects prove those lessons or retire them. A language it has not learned yet is learned
first (this needs the web). Words that are not one language are said so, and nothing is done:

```sh
tinker exercise rust           # start, or go on from where it stopped
tinker exercise status         # how it is going: each project's measures, and what it practises next
```

An exercise is a [job](#jobs-): `tinker stop #N` stops it with everything kept, and `tinker resume #N` goes on.
One exercise runs at a time, and each language keeps its own projects and lessons. Starting another pauses the
one that runs.

You come first, as with any job: a chat, a command line or a job you start pauses it (its build stops and
saves itself), and the exercise goes on as soon as nothing else runs and you have your answer.
It runs until you stop it. In a chat, `exercise rust` starts it in the background, so it keeps going after
the conversation ends. It keeps one message, whether it is started, stopped or resumed, and that message shows
what its build is doing right now, as a task's does. A conversation
started later picks up the exercise's message again.

## Jobs ⏯

Everything that takes a while is a job: a task, `add`, `fix`, `learn`, `ask`, `teach`, `read`, `research`
and `exercise`. Each gets a number (`#1`, `#2` ...).

One job runs at a time. Start another, of any kind, or chat with Tinker, and the one that runs is paused: it
saves itself and waits, and you are answered at once. As soon as Tinker is idle (no job runs, and nothing you
asked is still being answered), the job paused last goes on by itself, then the one before it. There is no
waiting time.

List them, newest first, 10 to a page:

```sh
tinker jobs              # the running ones, then the paused ones, then the stopped ones
tinker jobs paused       # only the paused ones (or running, stopped, finished, archived)
tinker jobs finished 2   # the second page
```

Stop one, or the one that runs. What it did is kept. A paused job you stop no longer goes on by itself:

```sh
tinker stop #3
tinker stop
```

Go on where it stopped, even days later. What it already asked the model and the web is answered from the
job's own journal, so no search or model call is made twice; it works live again from the first new step:

```sh
tinker resume #3         # a paused one goes on at once
tinker resume            # the job stopped last
```

A job that was killed (or the computer restarted) shows as stopped, and resumes the same way. Once a job
finishes, its journal is deleted. In a chat, the same words work: `jobs`, `stop #3`, `resume #3`.

Clear the jobs you are done with. They are archived, not deleted: `tinker jobs` no longer shows them, but
`tinker jobs archived` does. An archived job cannot be resumed:

```sh
tinker clear jobs #3 #5     # archive these two (stop a running one first)
tinker clear jobs           # archive every stopped and finished job
tinker jobs archived        # list them
```

## Settings 🔧

See the settings, or change one:

```sh
tinker config
tinker config test_timeout 90
```

Tune how `tinker learn` uses the web:

```sh
tinker config learn_rate 4          # web requests per minute
tinker config learn_backoff 10      # most seconds to wait out a rate limit
tinker config learn_searches 1      # never a second search
tinker config fetch_fallbacks 0     # never ask the web plugin to fetch a page
tinker config docs_pages 50         # pages taken from a documentation site
tinker config learn_think high      # think harder (and slower) while reading pages
```

Delete parts of what Tinker keeps, or all of it (your projects stay). `tinker help clear` lists every part:

```sh
tinker clear memory runs            # lessons for any language, and the list of projects
tinker clear read #2                # one reading: its text and what was learned from it
tinker clear history                # what the chats showed
tinker reset                        # everything
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

Ruff keeps the code in one style. Format and lint before you commit:

```sh
ruff format . && ruff check --fix .
```
