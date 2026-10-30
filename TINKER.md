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

Tinker searches open search engines that need no account and no key:
[Mwmbl](https://mwmbl.org) for the web at large, and [Wikipedia](https://en.wikipedia.org) and
[Wikibooks](https://en.wikibooks.org) for overviews and tutorials. For each thing it needs to find, the model
plans the searches: their words, the engines that suit each one and how many results to take. Each plan is
kept, so the same need is not planned twice. A learn's first search, from the topic alone, is not planned: it
asks every engine. It asks the engines at once and mixes their results by rank. If
one is down, the others still answer. Pages are read straight from their sites, in parallel, like `curl`
would.

Tinker uses the web as you would: a few searches at a time, within a small limit for each engine, and only
the pages its searches found. It never crawls a site. It only goes on the web when you ask: `tinker learn`,
`tinker research`, `tinker teach`, a run with `--web`, `tinker read` of a link, or `tinker exercise` of a
language it has not learned yet. Offline, it says so once and goes on with what it has.
`tinker doctor` shows the engines it searches.

## First Steps 🚀

Check that everything is ready (it tells you how to fix what is missing):

```sh
tinker doctor
```

Pull a model and use it. A model never measured is measured first, in a few minutes: how fast it reads
and writes, how much it can take in and reply, and 8 test tasks to solve, easy to hard, the same for every
model. Each task gets up to 3 tries in 2 minutes, and the test stops at a level where it solves neither
task. It ends with a summary in plain words, exact figures in brackets, and keeps every run in its history.
If the measurement fails, the model in use stays:

```sh
ollama pull qwen3
tinker model qwen3
```

Measure a model again, without changing the one in use:

```sh
tinker model calibrate qwen3
```

To learn (`tinker learn`, `--web`), Tinker also needs a small embedding model. `model` checks that it gives a
vector, then uses it for embeddings:

```sh
ollama pull qwen3-embedding:0.6b
tinker model qwen3-embedding:0.6b
```

Leave out the name to see Ollama's models, general ones first and then embeddings, each with what was
measured (`in use · writes ~19 words/s · reads ~225 words/s · solved 5 of 8 · 1 after a retry · ~40s an answer`). Above the list, with two or
more measured, it names the smartest and the fastest. On a terminal or typed alone in the chat, it is a select
box (up, down, Enter, Esc) that starts at the model in use, with the unmeasured models dimmed:

```sh
tinker model
tinker model calibrate
```

See the recipe alone, from the models that solved at least one test task: the smartest model (the most tasks
solved, then fewer retries, then the quicker answers) and the fastest (the shortest time to a correct answer),
as `🧠 Smartest: qwen3 (solved 7 of 8 · ~80s an answer)` and `⚡ Fastest: gemma3 (~20s an answer · solved 5 of 8)`:

```sh
tinker model recipe
```

Measure every model never measured first, one after another, then print the recipe. A model measured on
older test tasks reruns only the test tasks and keeps its other figures. It changes no model in use, and a
failed measurement goes on to the next:

```sh
tinker model recipe all
```

## Build 🔨

Build something new. It gets its own folder in `~/Projects`, named after the task (`calc-py`), and
Tinker builds it straight through:

```sh
tinker task a calculator in Python
```

It says each step as it goes, and how long it took. While the model works, one line is redrawn in place with
what Tinker is working on and for how long:

```text
📝 Plan: the spec (project)
   📋 Language: Python; 3 criteria; files: calc.py, test_calc.py, README.md
   ✅ Spec written (42s)

🔁 Verify (until every test passes)
   🎯 C2 (2/3 pass): calc.sub(5, 3) is 2
      ⠋ Making C2 pass: calc.sub(5, 3) is 2… 1m 4s (64s)
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

Look up the web while building (every run takes `--web`: `add`, `fix`, `amend`, `restart` and `resume` too):

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

Talk with Tinker. Every command works here, and anything else is a normal chat. A command starts with `/`, as in `/status`; plain words that say one, like `clear the chat, please` or `show recipe`, run it too, and asking the model to do what a command does runs it or offers it for `/confirm`. `/exit` (or Ctrl-D) ends it:

```sh
tinker
```

On a colour terminal it opens with a banner showing the model, the embedding model, the projects folder and the chats it listens
on. Each chat opens cleared, with the same lines as a message above the status, and is cleared again when Tinker ends, leaving only the `not listening` status. When `/model NAME` picks
another model, the chats are cleared, the terminal is wiped and the banner shows anew. As in Claude Code, the prompt, with a rule over it, follows the output: it starts under the banner and
moves down as Tinker prints, until it reaches the bottom row; then older lines scroll into the scrollback.
The prompt edits like a shell: left and right, Home and End (or Ctrl-A and Ctrl-E), Backspace and Delete,
Ctrl-U and Ctrl-K to clear to the start or the end, and Ctrl-W to delete the word before. Up and down bring
back the lines you typed before, even from an earlier session. Ctrl-C clears the line, or quits on an empty
one. When Tinker prints while you type, your line and the menu are drawn again under it.

As in Claude Code, typing `/` lists the commands under the prompt, narrowed as you type (names that start
with what you typed first, then names that hold it):

```
› /re
› /restart [--yes]  build this folder's task again from scratch (asks first)
  /rename [@PROJECT] NAME  rename the folder of a project whose plan waits
  /research TOPIC  the web researched in depth, its facts checked across sites, and kept: slow
  /read FILE | URL  store a book, an article or a document, then learn from every chapter
```

Up and down pick one, Tab fills it in, Enter sends a command that takes no words (or fills one that does),
and Esc closes the list. Once you start a command's next word, the list offers that command's words the
same way:

```
› /model r
› /model recipe  the models Ollama has, and the one every build uses
```

Tab fills the word in and Enter sends the command with it. Resizing the window redraws the rule at the new size. Your lines show again
highlighted, Tinker's messages are marked with a steel blue `●`, and a `✻ Tinkering…` line above the prompt,
with a steel blue glow moving across the word, shows the time, the tokens, how long the model thought and
the current step while it works:

```
✻ Tinkering… (1m 4s · ↓ 1.2k tokens · thought for 12s) · 🧠 Learning about purpose (1 of 6)
  📊 3 page(s) read · 12 fact(s)
  … Read 2 of 6 page(s) found
```

The dim lines under it (the tally and the latest news) update in place. A job's steps appear as in the
chats, each once as it finishes, and the chats show the same Tinkering line. `NO_COLOR=1 tinker` turns all
of that off.

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

In the conversation or a chat, give it input after `<<<`: `/run @calc-py <<< 2+2`.

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

Only your paired chat is listened to. Each of the bot host's addresses gets a second to connect and a second
for its TLS handshake, so a broken IPv6 route falls to IPv4 at once instead of slowing the chat. The terminal's
banner names each chat (`chats Telegram`), and each change of its state prints a dimmed `Telegram · STATE`
line (`connected (IPv4)` or `unreachable: REASON`). Manage your channels (removing one asks first; `--yes` skips that):

```sh
tinker channel list
tinker channel disable telegram
tinker channel remove telegram
```

Type `/` in the chat to pick any command from the bot's menu. A command starts with `/`; anything else is a
chat, and a `/` with a name Tinker doesn't know is answered with a pointer to `/help`:

```text
/status
/runs
/model qwen3:8b
```

Send `/model` or `/model calibrate` alone and Tinker answers with the models' rows and a button for each;
tap one to choose it.

Attach a book, an article or a document to the chat, and Tinker reads it: see [Read](#read-).

The chat stays tidy. Tinker's status is one message: each new status replaces the one before, at the
bottom of the chat, even after a restart. Each task has
one message. Quick looks like `/jobs` or `/help` go after two minutes, and your question goes as soon as
Tinker answers. Everything else goes after a day. Nothing is lost: Tinker keeps a history of what it showed.

```text
/history         # what the chat showed, newest first, with what you asked
/history 2       # the next page
/history #12     # show #12 again
/clear chat      # empty the chat now (the history stays)
/clear history   # forget the history too (asks first)
```

`tinker history` shows the same list in a terminal. For privacy, `/clear history` deletes everything Tinker
kept of the chat. Your projects, runs and jobs stay.

## Learn 🧠

Teach Tinker a language, a tool, or a whole domain (it reads the web, and keeps what it learns). No quotes needed:

```sh
tinker learn rust
tinker learn Python 3.14
tinker learn food bank inventory
```

A domain is learned like a tool. To research one, its facts checked across sites, use `tinker research` (it is
slower).

A language or a tool is learned mainly from its own documentation, for the version you have installed
(a language's tools too: its test runner, formatter, linter). The first time takes a search to find what it
is, one for your version's documentation, one for each tool, and a few more only for what the
documentation leaves unanswered. The cleaned pages are kept per version, so learning it again searches for
none of them. When the web finds nothing (offline, nothing about the topic), it stops at once and says why,
before the model does any work, and keeps what it knew before. Every web search and fetch Tinker makes is
logged in `.tinker/web-requests.log`. The summary at the end shows the versions installed here.

Forget what learning keeps:

```sh
tinker clear docs        # the kept documentation: it is fetched again on the next learn
tinker clear searches    # the kept search plans: the model plans them again on the next search
```

Teach it a command of yours, which builds may then use (`=` says it is a command; a quoted definition works
too), and forget it again:

```sh
tinker learn serve = python3 -m http.server
tinker learn serve "python3 -m http.server"
tinker learn serve none
```

See what it has learned, a part at a time: `memory` lists the parts (each language, any language, each
topic, your commands) with what each holds, and every list comes 10 to a page. Each item has a short ID
that stays the same while it is kept, and ✅ when a build proved it, 🌱 when not yet:

```sh
tinker memory                      # the parts, and how many items are retired
tinker memory python               # one part: its details, then its items
tinker memory python lessons 2     # its lessons, the second page
tinker memory flask facts          # a topic's facts
tinker memory lessons              # every lesson, across memory
tinker memory any retired          # what did not hold, and was retired
tinker memory show 5b94dc          # one item whole: details, sources, links, the runs that used it
tinker memory find pytest toml     # every item that holds all the words, retired ones too
tinker memory recent               # what was learned, newest first
```

Browsing changes nothing: `tinker teach` corrects an item, and `tinker clear PART` forgets a part.

See it as a graph you can explore in the browser: each lesson, fact and fix linked to its language, and to
the runs that taught it, proved it or found it wrong (what did not hold is kept, greyed, with why). In a
chat, `memory --graph` sends the page as a file to open on your phone:

```sh
tinker memory --graph
```

Ask what it has learned. It answers just what you ask, from what it keeps first: its items, the passages of the
documentation and readings it kept that are most about your question, and the versions of those topics installed
here. What it adds from the model's own knowledge is marked "Not from what I learned", and an answer that depends
on your version names it:

```sh
tinker ask what is your current toolchain for python
tinker ask which lessons do you have about tests
tinker ask does sqlite support RETURNING
tinker ask how do I list a cargo project's dependencies
```

To be sure, it may run one check first and answer with what it printed ("I ran …, it printed …"): SQL in an
in-memory SQLite, or a command with a tool it learned and found on your machine, run in a [sandbox](#safety-)
from a new empty folder. Installers (pip, npm, brew, cargo install and the like) are refused, and without a
sandbox nothing runs. A refused or failed check is told to it once, and its fixed check runs; if that fails
too, its first answer stands. An answer a check confirmed
is kept in its topic (a tool or domain it learned; a language keeps none) as a 🌱 fact, for `tinker teach` to confirm or correct. Asking changes nothing else.

Correct it when you know better, as many things at once as you like. Mark your own choices with the word
**prefer** ("I prefer no mypy"): Tinker honors them as they are. Everything else is a fact, checked first: Tinker runs commands on your machine that
only show what is installed, and reads web pages about them. Each change is made once the facts it rests
on hold: what you showed wrong is retired (kept, with why), the toolchain is corrected, and your lesson is
kept, with its sources. A change whose facts do not hold is not made, and Tinker says why:

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
tinker read list [PAGE]                              # the reading list, a page at a time: how much of each is read
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

An exercise is a [job](#jobs-) like any other, and each language keeps its own projects and lessons.
`tinker stop #N` stops it with everything kept, and `tinker resume #N` goes on. Anything you start, another
exercise too, or a chat or a command line, pauses it (its build stops and saves itself), and the exercise goes
on as soon as nothing else runs and you have your answer.

It runs until you stop it. In a chat, `/exercise rust` starts it in the background, so it keeps going after the
conversation ends. It keeps one message, whether it is started, stopped or resumed, and that message shows what
its build is doing right now, as a task's does. A conversation started later picks up the exercise's message
again, and an exercise that was killed (or the computer restarted) goes on by itself.

## Jobs ⏯

Everything that takes a while is a job: a task (and `restart`, `confirm`, `amend`, `resume`), `add`, `fix`,
`model NAME` of a model never measured, `model calibrate`, `learn`, `ask`, `teach`, `read`, `research` and `exercise`. Each gets a number (`#1`, `#2` ...).

One job runs at a time. Start another, of any kind, or chat with Tinker, and the one that runs is paused: it
saves itself and waits, and you are answered at once. As soon as Tinker is idle (no job runs, and nothing you
asked is still being answered), the newest paused job goes on by itself, then the next. There is no waiting
time.

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
tinker resume            # the job stopped last (with none stopped, this folder's run)
```

`tinker confirm` is not a job's resume: it always builds the plan that waits in its folder.

A job that was killed (or the computer restarted) shows as stopped, and resumes the same way. Once a job
finishes, its journal is deleted. While a job runs, the computer stays awake. In a chat, the same commands work with a `/`:
`/jobs`, `/stop #3`, `/resume #3`.

Clear the jobs you are done with (it asks first). They are archived, not deleted: `tinker jobs` no longer shows
them, but `tinker jobs archived` does. An archived job cannot be resumed, and its journal and its log of model
calls are deleted:

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
tinker help config                  # every setting you can change, with what it takes
```

A value that does not fit is refused, with what the setting takes, and the old value stays.

Choose where new projects go, and how long the model stays loaded after a call (before each call, other
loaded models are unloaded):

```sh
tinker config projects ~/Code       # new projects (and the exercises) go here
tinker config keep_alive 10m        # keep the model loaded 10 idle minutes (30m by default; 0 unloads it at once)
```

Tune how `tinker learn` uses the web:

```sh
tinker config learn_searches 1      # at most one follow-up search
tinker config learn_pages 4         # pages read from each search
tinker config tool_docs_pages 2     # pages read about each of a language's tools
tinker config learn_think high      # think harder (and slower) while reading pages
tinker config web_timeout 60        # wait up to 60 seconds for each search engine
```

Delete parts of what Tinker keeps, or all of it (your projects stay). Each shows what it deletes and asks
first (`--yes` skips that). `tinker help clear` lists every part:

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

- 📦 The checks an `ask` runs are walled off: no network, writes only in a new empty folder, limited time,
  file size and processes (`sandbox-exec` on macOS; namespaces and Landlock on Linux). Elsewhere they do not run.
- 🧪 Want builds walled off too? Run Tinker as a separate user, or in a VM or container.
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
