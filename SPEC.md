# Tinker: Specification

Tinker builds what a request asks for with a local model run by Ollama. It plans the work as a small spec,
writes one unit test for each spec point, writes just enough code to pass them, and checks the result the way
a person uses it. It learns from every run. This document says what Tinker is and does. The tests in `tests/`
enforce it.

## How to read this

- Each numbered point (**SET-1**, **WEB-3** ...) states one behaviour. At least one test enforces it, marked
  `@pytest.mark.spec("SET-1")`. `tests/test_spec_coverage.py` checks that every point here has a test, that
  every test names a point that exists, and that each section numbers its points from 1, in order, with no gaps.
- A point that refers to another names it in bold (**JOB-8**). A rule is stated once, where it belongs; other
  points refer to it.
- The sections after [The cycle](#the-cycle) follow the modules in the order of the
  [Architecture](#architecture) table. Each heading names its module and the prefix of its points.
- Run the tests from the repository root: `python -m pytest` (after `pip install -r requirements.txt`).

## The idea

1. Build only what the request clearly asks for. Anything else waits for `tinker add` or `tinker fix`. Nothing
   is made or changed beyond what the spec of the project needs: every file serves a spec point.
2. The plan is a spec of points. Each point has exactly one unit test, written before any code.
3. The code is just enough to pass those tests.
4. The project is done when every spec point's test passes, set up afresh, the way a person runs it.
5. In the conversation, the person confirms a plan before anything is built. A run started from the command
   line builds autonomously, unless it is asked to wait (`--confirm`).
6. Tinker keeps what it knows in its own database, and each project's state in the project's own database.
   People read them through Tinker's commands. They never edit them by hand.

## Principles

- **Tinker's home is Tinker's alone.** Its settings change only through its commands. Its memory (what it
  measured, learned and was taught) changes only through its own steps: calibration, learning, a command the
  person gives, and the lessons step at the end of a cycle. A project's files never change Tinker's home.
- **A project's state is the project's alone.** Tinker keeps it in the project's `.tinker` folder. The home
  keeps none of it: of a project, it keeps a pointer (its run ID, its folder and its task) and one line about
  each run (its task, language, status and dates), so what a run taught can be traced after its folder is
  gone; its jobs and exercises keep only their own state.
- **Nothing of Tinker's is put among a project's own files**, except `SPEC.md`, which is the plan, written for
  people.
- **A project is named only explicitly**, as `@ID` (its full run ID) or `@folder` (its folder's name,
  exactly). Tinker never guesses a project from the words of a request.
- **Knowledge must be proven.** What Tinker learns starts as a candidate. A cycle that succeeds while using it
  proves it. What it is taught is checked first, and kept proven only when the checks hold
  ([teaching](#teaching-tch)). A failure in the step it was meant to help retires it, even when it was proven
  before: it is kept apart, with why, for people to see, and never shown to a model again. Each item keeps its
  history: the run that taught it, and the runs it helped or failed.
- **A lesson is general.** It may hold for one language or for any language. It never names a project, its
  files, its names or its data.
- **Every part reports through one tracker.** A run publishes what it is doing as structured state. The
  terminal and the chats show that state. They never read meaning back out of printed text.
- **The person comes first.** Every long command is a job. Whatever the person asks for pauses the job that
  runs, and a paused job goes on by itself once Tinker is idle ([jobs](#jobs-job)).
- **Tinker uses the web as a person does.** It asks a few open search engines, in parallel within a fixed
  limit, and reads only the pages its searches found, as curl would on the person's behalf. It never crawls:
  no site maps, no links followed. A good search is how it finds what it needs, so no search engine has reason
  to turn it away: the model picks the engines, queries and result counts that suit each need.

## Architecture

`tinker.py` only checks that Python is new enough, in syntax an older Python can still read, then starts
`tinkerkit/cli.py`. The `tinkerkit` modules each have one responsibility, and each imports only the modules
listed above it:

| Module | Responsibility |
|---|---|
| `messages` | everything Tinker tells the person, in one voice (`say`, `Messages`, `Message`): each message's kind gives its icon, sections nest it, followers (a tracker) get it as data, and details go to the log only |
| `text` | plain text helpers every module shares |
| `store` | one SQLite database of documents and logs (`Store`, `Log`) |
| `settings` | the numbers and fixed names more than one part shares (a module's own stay with it); the Tinker home (`Home`) and its database, the settings table (`SETTINGS`, `Config`), numbered lists (`Numbered`), the one way to delete (`confirm_deletion`) |
| `system` | what this computer tells at once by fixed read-only commands, with no model (`SystemCheck`, `SYSTEM_CHECKS`) |
| `commands` | the one table of Tinker's commands with each one's kind (`kind_of`, `deletes`), and project references (`@ID`, `@folder`) |
| `jobs` | every long command as a job, listed, stopped and resumed (`Jobs`), its journal of requests and answers (`Journal`, `journaled`), and its log of model calls (`calls_log`) |
| `ledger` | what is said in the chats, so a chat is only a view of it: each message with when it goes, each thing shown, numbered, for `history` (`Ledger`) |
| `prompts` | prompt parts (`Part`), replies (`Reply`), every system prompt (`Prompts`) |
| `workspace` | the project's files and the commands run in it (`Workspace`, `Toolchain`, `TestRef`, `Output`, `FileView`, `TaughtCommands`) |
| `model` | the local model: Ollama's API, one request with its budgets and retries (`Model`), measuring a model (`Calibrator`), measured models (`Models`) |
| `project` | the plan and its record (`Project`, `SpecDocument`, `Runs`, `RunChecks`, `Brief`, `Outcome`), the run's `Tracker` and its `ending`, and the step pipeline (`Steps`) with what moves a tracker through it (`Tracked`) |
| `fetcher` | the web plugin's page fetcher, used by `web` alone: pages read straight from their sites, in parallel, with the standard library only (`Fetcher`, `Fetched`) |
| `search` | the search engines the web plugin asks, used by `web` alone: open engines with no key, with the standard library only (`SearchEngine`, `Mwmbl`, `MediaWiki`, `Wikipedia`, `Wikibooks`) |
| `passages` | every page read as text: an HTML page's main content as blocks, and fetched pages as passages under headings, repeats dropped (`Cleaner`, `MainBlocks`, `Page`, `Passage`) |
| `web` | the web plugin, the only part that reaches web content: search and fetch, and every failure of either (`Internet`, `WebPage`, `RequestLog`), and a page as text (`PageText`, a view over `passages`) |
| `conversation` | the terminal and the chats as one conversation (`Desk`, `Channel`, `Markup`, `Talk`, `Shell`, `Progress`, `Reporter`) |
| `channels` | the chat plugin: Telegram, and `tinker channel` |
| `knowledge` | lessons and proven knowledge (`Knowledge`), research (`Researcher`), and what learning shares (`Learner`, `Library`, `Retriever`, `Embeddings`) |
| `slots` | what learning a language or a tool must find out, as data (`Slot`, `PROFILES`), and each slot's passages (`Selector`) |
| `learning` | learning a language or a tool by its slots (`SlotLearner`, `DocCache`, `LearnStopped`) |
| `graph` | what Tinker knows as one graph of items and links (`Graph`), the page that shows it (`GraphPage`), and browsing it in pages (`MemoryBrowser`) |
| `reading` | what the person gives to read: stored whole on the reading list (`Readings`), then learned from chapter by chapter (`Reader`, `Epub`, `Book`) |
| `sandbox` | running a check with no network and writes only in its own folder, one backend per platform (`Sandbox`, `MacSandbox`, `LinuxSandbox`, `NoSandbox`) |
| `teaching` | asking what Tinker keeps (`Asker`), checked by running when it can, and teaching it, checked before anything changes (`Teacher`, `Memory`, `Verdict`) |
| `exercising` | practising a language on its own, giving way to the person (`Exercise`, `Exercises`) |
| `session` | what every phase works through (`Session`), and the phase contract (`Worker`, `Phase`) |
| `planning` | recall, the spec, the contracts where files meet, and their review |
| `building` | setup, the tests, the code, and fixing until every test passes |
| `finishing` | tidy, the README, the final review and the lessons |
| `cli` | the main system: the command line (`Cli`, `Commands`, `Doctor`), one run (`ProjectRun`, `ProjectFolder`) and the cycle of phases (`CycleRunner`) |

- **ARCH-1** Each `tinkerkit` module imports only the modules listed above it in the table. The one exception
  is `conversation`, which imports the `channels` plugin lazily, inside a function.
- **ARCH-2** The channels plugin is optional. Without `tinkerkit/channels.py`, the conversation has no chat
  channels, and everything else works.
- **ARCH-3** Every model call goes through `Ollama`, and who may call `Ollama.chat` is **MOD-25**. Every web
  search, page and download goes through `Internet.search` or `Internet.fetch`; only `web` uses the `fetcher` and `search`.
  The model's server and the chat channels are not web content and keep their own connections. Every project
  command goes through `Workspace.run` or `Workspace.start`.
- **ARCH-4** Only `settings.Home` knows where the home is. No other module names a path inside it.
- **ARCH-5** No module and no test has a comment or a docstring: names, types, tests and this spec say what the
  code does. The `#!` line of `tinker.py` and `# noqa` pragmas are the only comments.
- **ARCH-6** `tinker.py` refuses every Python below 3.15, on any platform, before it imports `tinkerkit`:
  `Tinker needs Python 3.15 or newer; this is VERSION. On a Mac: brew install python`.

## State

The Tinker home (**SET-1**) holds:

| File or folder | What |
|---|---|
| `tinker.db` | the home database (mode 600), below |
| `channel-NAME.lock` | the lock of each chat channel a Tinker listens to |
| `web-requests.log` | every web search and fetch Tinker made, one JSON line each (**WEB-6**) |
| `memory.html` | the page `tinker memory --graph` writes |
| `reading/` | the files sent in a chat to read |

The home database holds these collections and logs. `tinker clear PART` deletes a part (**SET-10**), named in
the last column:

| Collection or log | What | Part |
|---|---|---|
| `config` | the settings that differ from `DEFAULT_CONFIG` | |
| `models` | what calibration measured for each model | `models` |
| `languages` | per language: the toolchain that worked, toolchain fixes, lessons, proven facts | `languages` |
| `memory` | lessons for any language | `memory` |
| `learned` | what was learned from the web about each topic, with sources, and what each reading teaches | `learned` |
| `embeddings` | the embedding of each learned text, per embedding model | `learned` |
| `sources` | the cleaned documentation pages a learn read for each topic, per version (`slug@version`) | `docs` |
| `search plans` | the searches planned for each need (**KNW-26**) | `searches` |
| `readings`, `reading numbers` | the reading list: each source's whole text by chapter, each chapter's state, its survey; the last number given, kept by a clear | `read` |
| `commands` | the person's own commands (aliases, programs) | `commands` |
| `projects` | a pointer to each project: its run ID, folder and task | `runs` |
| `runs` | one line about each run: its task, language, status and dates (the last `RUNS_KEPT`) | `runs` |
| `channels`, `channel_secrets` | the chat channels and their tokens | |
| `listening` | the conversation's live state: its status messages | |
| `exercise` | per language: its exercise's practice projects, what each measured, and the focus of the next | `exercise` |
| `activity` | the person's requests being answered (`answering`), and the ids of the exercise's one message (`exercise message`) | |
| `chat views` | each message in a chat (Tinker's and the person's): when it goes, and the thing it shows | |
| `chat history`, `chat history numbers` | each thing shown in a chat, as it was shown last, with what the person asked for it; the last number given, kept by a clear | `history` |
| `jobs`, `job numbers` | every job: its number, command, title, arguments, state, process, dates and tracker; the last number given, kept by a clear | `jobs` |
| log `calls #N` | the model calls and details of job #N (a run's go to its project's `events`), until the job is archived (**JOB-12**) | `jobs` |
| log `journal #N` | job #N's requests (model calls, web searches, page fetches) with their answers, until it finishes | `jobs` |

Jobs, readings and the chat history are numbered lists (`Numbered`, **SET-12**). `#N` names an entry. A list
shows them newest first, a page at a time, and a page that has more after it names the command for the next.
Clearing archives jobs (**JOB-11**), and deletes readings (**RDG-13**) and the history (**SET-10**).

Each project keeps its own state in `<project>/.tinker/project.db`:

| Collection or log | What |
|---|---|
| `run` | the run's record (task, status, features, times) |
| `spec` | the plan: criteria, cycles, files, toolchain, what was learned and used |
| `texts` | the spec file's text and its brief |
| `restore` | the restore point of the criterion being fixed |
| log `events` | every model call, session event and detail |
| log `output` | every message the run said |
| log `progress` | every change of the run's tracker |

A started server writes its output to `<project>/.tinker/app-output.txt`. The plan is also written as
`<project>/SPEC.md`, for people.

## The cycle

A cycle is the project itself (`base`), a feature (`tinker add`) or a bug fix (`tinker fix`). Its phases run in
order, each saved as it ends, so a run resumes from the phase it reached:

`recall → spec → design → review → setup → tests → code → verify → tidy → docs → final_review → lessons → done`

Its planning phases are `recall → spec → design → review`.
A bug fix runs `recall → reproduce → verify → final_review → lessons → done`.

A cycle started from the conversation, or with `--confirm`, waits for the person once its plan is made: the
project or a feature after `review`, a bug fix after `reproduce`. `confirm` builds it. `amend CHANGE` changes it
and plans it again, and then it waits again. A cycle started from the command line without `--confirm` builds
straight through. `--web` lets any run, at any phase, learn from the web what the rest of the cycle needs.

---

## messages (MSG)

Everything Tinker tells the person goes through one voice, `say` (a `Messages`).

- **MSG-1** Each message has a kind, and its kind gives its icon: done ✅, warn ⚠️, fail ❌, retry 🔁, skip ➖,
  stop 🛑, tip 💡, item •. A title, a step and an info take their own icon; plain text has none. A line is its
  icon, a space and its words; after an emoji drawn with a variation selector (⚠️), two spaces, so the words of
  every line stay in one column.
- **MSG-2** A section nests what is said inside it three spaces deeper. A part is a title with a section under
  it. A title at the top has a blank line before it. A summary is a part whose lines are `label: value`, each
  with its icon. `took` says something done with how long it took.
- **MSG-3** Every message is given, as data (`Message`: kind, text, depth, icon), to each follower. A follower
  that fails never breaks what is said. A run's and a learn's `Tracker` follow: a message becomes the latest
  and goes to the project's `output` log (every message a run says); a live one becomes what goes on now.
  `working_on`, `tally` and `found` set what the run works on, its tally and what it found. `tokens` adds to
  the job's token count (reply and thinking chunks, earlier runs too); `thinking` tells whether the model
  thinks now, and when its thinking ends the tracker keeps how long it thought until the next step starts.
- **MSG-4** `live` redraws one status line in place on a terminal only. Piped (a chat following the command),
  it writes nothing; its followers are still told. The next message clears it first. The line is a turning
  star, `Tinkering…`, then in brackets the time since the work began, the tokens (`↓ 1.2k tokens`, left out
  at 0) and `thinking` or `thought for 12s` (left out when the model does not think), then what goes on now:
  `✻ Tinkering… (1m 4s · ↓ 1.2k tokens · thought for 12s) · 🧠 Learning about purpose (1 of 6)`.
  With colour, `Tinkering…` is steel blue with a glow, as in Claude Code: a few letters in a light steel blue
  that move one letter on with each frame, left to right, and start again from the left once past the end.
- **MSG-5** `detail` goes to the log given to `keep_log` (a run's `events`, with every model call it makes; a
  job's `calls #N`), and is never printed. With no log, it is dropped.
- **MSG-6** `human_duration`: 42.5 gives `42s`, 1762.08 gives `29m 22s`, 4000 gives `1h 6m 40s`.
- **MSG-7** No module of Tinker but `messages` calls `print`.
- **MSG-8** `say.pick(choices, start)` is a select box on the terminal, drawn as a short list under the
  prompt, not full screen. Each choice is a label and how it shows: plain, dim, or a heading (never current:
  up and down step over it); `›` marks the current one, first `start`. Up and down move it, stopping at the ends; Enter gives its index; Esc, Ctrl-C or end of input give
  `None`. It reads the keys one at a time with the terminal raw, gives the terminal back as it was, and clears
  the list when it ends. A row wider than the terminal is cut to fit, so each row takes one line.
- **MSG-9** The raw keys name, beside those of **MSG-8**: left, right, Home, End, Backspace, Delete, Tab and
  Ctrl-A/E/U/K/W; any other bytes are text, a UTF-8 character whole and a paste as one piece. `LineEditor`
  edits one line from them: the text and the cursor, text inserted at the cursor, left and right one
  character, Home/Ctrl-A and End/Ctrl-E to the ends, Backspace and Delete one character, Ctrl-U to the start,
  Ctrl-K to the end, Ctrl-W the word before. Up and down go through the lines typed before (newest first, the
  line being typed kept for when down passes the newest). Enter gives the line; Ctrl-C or end of input on an
  empty line give `None`, Ctrl-C on a line empties it, and end of input on a line does nothing. Tab is the
  command menu's key (**MSG-10**); the editor ignores it. The cursor counts a wide character as two columns.
- **MSG-10** `slash_matches(typed)` gives, for a line that starts with `/` and holds no space, the commands a
  chat offers (**CMD-1**) whose name starts with what follows the `/`, then those whose name holds it, each
  group in table order. `CommandMenu` shows them under the prompt, at most 8 rows, each `/name` with its
  synopsis' words after the name dim and what it does; `›` marks the current one, first the first. Up and down
  move it, scrolling the 8 rows, stopping at the ends. Tab fills the line with `/name ` and closes the menu.
  Enter on a command that takes no words (`words` is `none`) sends `/name`; on one that takes words it fills
  the line with `/name ` and waits. Esc closes the menu and keeps the line; typing on narrows it again, and no
  match or a space closes it. `word_matches(typed)` gives, for a line `/name w` of a chat command with one
  partly typed word after the name, that command's words (its actions' words, then the lowercase words opening
  an alternative in its synopsis, at most one bracket deep, each once) that start with it, then those that hold
  it. `CommandMenu` shows them as it shows commands, each row `/name word`; Tab fills the line with
  `/name word ` and Enter sends `/name word`.

## text (TXT)

Plain text helpers every module shares.

- **TXT-1** `first_line` gives the first line of the text with its ends stripped, cut to the limit with
  `...`. `one_line` gives the text on one line, each run of whitespace one space. `words` gives the lowercase
  alphanumeric words. `fingerprint` is the SHA-1 of the text. `similarity` is a case-insensitive ratio from 0
  to 1. `field(item, key, limit)` gives a key's value as stripped text, cut to the limit, empty when it is
  missing or none. `host` gives a URL's site in lowercase, without `www.`.
- **TXT-2** `split_text(text, size)` gives chunks of at most `max(size, 200)` chars. It splits between
  paragraphs, cuts a longer paragraph, and loses no text.

## store (STO)

One SQLite database of documents and logs.

- **STO-1** `Store(path)` is one SQLite database of documents (JSON values) by collection and key. `get` gives
  the default for a missing document. `put`, `delete` (one document, or a whole collection) and `items` work as
  named. `update(collection, key, change, default)` reads, changes and writes a document in one transaction,
  so two processes never lose each other's change. A write is all or nothing: a crash leaves the old value or
  the new one.
- **STO-2** `Store.append(log, entry)` adds an entry with its time to a log. `Store.entries(log, after)` gives
  the entries after an ID, in order, with their IDs. A database file is made with mode 600.

## settings (SET)

The home and its database, the settings, numbered lists, and the one way to delete.

- **SET-1** `Home.root` is the `.tinker` folder next to the `tinkerkit` package and `tinker.py`, where they
  really are (symlinks resolved). `Home.store()` is the home database, `tinker.db`, in it.
- **SET-2** `DEFAULT_CONFIG` is exactly: model `None`, ollama_url `http://localhost:11434`, keep_alive `"30m"`,
  max_growth `1.15`, build_think `"medium"`, test_timeout `60`, web_timeout `30`, learn_missing `2`,
  learn_cutoff `0.0`, learn_pages `8`, learn_min_pages `2`, learn_searches `3`, learn_think `"low"`,
  tool_docs_pages `5`, docs_latest_days `30`, fetch_workers `6`, fetch_timeout `15`, fetch_max_bytes `2000000`,
  embed_model `""`, projects `"~/Projects"`. web_timeout is a search's timeout; fetch_workers, fetch_timeout and
  fetch_max_bytes are the `Fetcher`'s workers, timeout and max_bytes; embed_model is the embedding model
  `tinker model NAME` selects.
- **SET-3** `PHASES` and `FIX_PHASES` are the phase lists given in [The cycle](#the-cycle).
  `CONFIRM_AFTER` is `review` for the project and a feature, and `reproduce` for a bug fix.
- **SET-4** `Config` is `DEFAULT_CONFIG` overridden by the home's `config`. It is read from the database each
  time, so a change another Tinker made is seen at once; a stored key no setting has is ignored.
  `Config.save(**changes)` keeps the changes.
- **SET-5** `SETTINGS` is the one table of the settings: for each key of `DEFAULT_CONFIG`, its type, its
  range and its owner, the command that sets it (`model` for model and embed_model, `config` for the
  rest). `Config.check()` checks every setting by its row, and rejects with
  `SystemExit`: a keep_alive that is neither whole seconds nor a duration (`10m`, `1h30m`); a build_think or
  learn_think other than `"high"`, `"medium"`, `"low"`, `true`, `false` or `null`; and a number outside its range, or not whole
  where its row says so (max_growth above 1, web_timeout above 0, test_timeout and learn_missing at least 1,
  and each `learn_*`, `docs_*` and `fetch_*` limit). A boolean is never a number. Every command that needs the
  model checks them first.
- **SET-6** `Config.set(key, text)` is `tinker config KEY VALUE`. It sets only the keys whose owner is
  `config`, reading the value by the row's type. A value that fails its row's check is not kept, and the
  command ends with why. `tinker help config` lists those keys with their type and range, from the table.
- **SET-7** `Config.keep_alive(value)` gives whole seconds as an `int` and a duration as it is.
  `Config.ollama_url()` has no trailing slash.
- **SET-8** `Home.ensure()` makes the home folder and its database on first use. A home that cannot be written
  ends with `SystemExit`.
- **SET-9** Every deletion goes one way, `confirm_deletion(said, items, command, yes, verb)`: it says what it
  deletes (or archives), item by item, asks, and only then deletes. With `yes` (`--yes`, or `confirm` in a
  chat) it does not ask. With no terminal to ask in, it deletes nothing and exits, saying how to confirm
  (`Nothing deleted: run COMMAND in a terminal to confirm, or add --yes.`); a no exits with `Nothing deleted.`
  (an archive says `Nothing archived`). With nothing to delete, it asks nothing (`clear` and `reset` say so).
  `Home.reset(yes)` deletes the home folder this way. It refuses a folder not named `.tinker`, and does
  nothing when the folder is missing.
- **SET-10** `tinker clear PART` names exactly these parts: languages, memory, learned, docs, searches, models,
  runs, commands, history, exercise, jobs and read ([State](#state) shows what each holds). `jobs` and `read` take
  numbers (`#N`), and their own modules clear them (**JOB-11**, **RDG-13**). `Home.clear(names, yes)` deletes
  only the named parts from the home database, as a deletion (**SET-9**). It rejects an unknown part by
  listing the parts.
- **SET-11** `Home.keep_awake()` keeps the computer awake with `caffeinate` (macOS) or `systemd-inhibit`
  (Linux), tied to Tinker's process. It does nothing when neither tool is there, and it never raises.
- **SET-12** `Numbered(collection, counter)` is one numbered list in the home database. `add(entry)` keeps the
  entry under the next number (its `number`), and a number is never given again, even once its entry is
  deleted or its list cleared. `get(N)`, `put(entry)`, `update(N, change)` and `delete(N)` work as named, and
  `newest()` gives the entries newest first. `page(entries, N)` gives page N of the entries, `PER_PAGE` (10) to
  a page, with how many pages there are (at least one).

## system (SYS)

- **SYS-1** Tinker tells about this computer at once only what this table allows, in this order. Each row
  is a fixed command (a program and its arguments, never a line a model wrote) that changes nothing:

  | word | emoji | label | macOS | Linux | the line shows |
  |---|---|---|---|---|---|
  | `time` | 🕒 | Time | `date +%H:%M %Z` | `date +%H:%M %Z` | the output: `11:16 IST` |
  | `date` | 📅 | Date | `date +%A %d %B %Y` | `date +%A %d %B %Y` | the output: `Saturday 31 October 2026` |
  | `disk` | 💾 | Disk | `df -h HOME` | `df -h HOME` | free of size and the share used: `609 GB free of 926 GB (33% used)` |
  | `ram` | 🧠 | RAM | `memory_pressure` | `free -h` | available of the total and the share used: `21 GB available of 24 GB (14% used)`; macOS gives the total in bytes and the free percentage, Linux the `total` and `available` columns |
  | `battery` | 🔋 | Battery | `pmset -g batt` | `upower -i /org/freedesktop/UPower/devices/battery_BAT0` | percent, state, and the power source when named: `100%, charged, on AC power` |
  | `uptime` | ⏱️ | Uptime | `uptime` | `uptime` | days, hours, minutes, leaving out the zero ones: `2 d 3 h 53 m`, `45 m` |
  | `network` | 🌐 | Network | `ipconfig getifaddr en0` | `hostname -I` | the first address: `192.168.220.41` |

  Sizes read `N GB`, `N MB` (`926Gi`, `23G` → `926 GB`, `23 GB`). `ram` is RAM alone: `memory` is the
  `memory` command's word and never names this check.
- **SYS-2** `SystemCheck` is the base of every check; each row of **SYS-1** is one subclass that sets its
  word, emoji, label and two commands and gives `summary(output)`, its line's text from the command's output.
  A subclass registers itself when defined, so `SYSTEM_CHECKS` (one instance of each, in table order) and
  `SYSTEM_WORDS` hold exactly the table's rows, and a new check is one new subclass and one new row; nothing
  else changes. `answer()` runs the command for the system Tinker runs on, without a shell, and gives
  `EMOJI LABEL: SUMMARY` (`🧠 RAM: 21 GB available of 24 GB (14% used)`). A system it has no command for, a program not
  found, a failure, an empty output, no answer within `SYSTEM_CHECK_SECONDS` (5) or an output `summary`
  cannot read gives `⚠️ LABEL: couldn't check on this computer` instead; it never raises.
- **SYS-3** `system [time|date|…]` is a `glance` command a chat offers, whose synopsis takes its words from
  `SYSTEM_WORDS`. `tinker system WORD` prints that check's answer, and `tinker system` every check's, one line
  each. In a conversation it is a fixed phrase (**CNV-5**), so `time`, `system disk`, `ram usage` and
  `what is the time now?` run it with no model, and the chat model asks for it for other questions about
  this computer (**CNV-20**).

## commands (CMD)

- **CMD-1** `COMMANDS` is the one table of Tinker's commands. For each it gives the synopsis, the group, what
  it does, its kind, whether a chat offers it, whether it is a run, whether it deletes, the words it takes in
  a chat (`none`, `one`, `any`), its arguments and its options. The kind is `job` (a long command, **JOB-1**),
  `glance` (answered at once, pausing nothing, **CNV-14**), `pauses` (a request the model answers right away,
  **JOB-10**), `own` (the conversation answers it its own way) or `terminal` (it asks in a terminal, so a chat
  only says to run it there). An action whose kind differs from its command's is an `Action` of that command,
  with its own kind: `read list`, `read show`, `exercise status`, `channel add`, `channel remove`,
  `channel start`, `model calibrate NAME`, `model recipe` (`glance`), `model recipe all`, `model NAME` of a model that does not embed and was never measured
  (**CLI-3**) and `learn` of a command of the person's (**CLI-1**). `kind_of(argv)` gives the kind of what the words
  ask for, and `deletes(argv)` whether it deletes. The command line builds its parser and its help from the
  table, and the conversation its commands. Nothing else lists commands by kind.
- **CMD-2** The runs are `task`, `restart`, `confirm`, `amend`, `add`, `fix` and `resume`. Every run takes
  `--web`. The runs that start a cycle (`task`, `restart`, `add`, `fix`) take `--confirm`, and the others do
  not. The commands that delete are `clear`, `reset`, `restart` and `channel remove`.
- **CMD-3** `ref(word)` reads a project reference: `@` followed by a run ID or a folder name. Any other word is
  not a reference. `refs(words)` takes a reference from the start of the words, and leaves the rest as they
  are.

## jobs (JOB)

Every long command is a job: it can be listed, stopped and resumed the same way, and a resumed job does not do
again what it did. The job asked for last is the one that runs (**JOB-8**), and a chat or a command line the
person asks for pauses it too (**JOB-10**), so the person is always answered at once.

- **JOB-1** `is_job(argv)` is true for a command of kind `job` (**CMD-1**): `task`, `restart`, `resume`,
  `confirm`, `amend`, `add`, `fix`, `model calibrate NAME`, `model recipe all`, `model NAME` of a model the home has no record of and that is not the embedding model (an embedding model not
  yet `embed_model` is one too: the home cannot tell it apart, **ARCH-1**), `learn`, `ask`, `teach`, `read`, `research` and `exercise`.
  Help, and a build an exercise runs (`EXERCISE_ENV`, which holds the exercise's job number), are never jobs. A job keeps the computer awake while it
  runs (**SET-11**). `Jobs.running(argv)` runs a job as the next number (`#1`, `#2` ...), kept in the home's
  `jobs` with its command, title, arguments, the day it started, its state `running`, its process and its
  tracker (`progress`, **PRJ-14**): the one list of what runs. Its process takes `JOB_STOP_SIGNAL` (SIGUSR2) as Ctrl-C.
- **JOB-2** A job ends `stopped` when it is interrupted (Ctrl-C, `JOB_STOP_SIGNAL`) or its tracker ends with
  the status `stopped` (`Tracker.end` tells `Jobs.ended`, which adds `("Go on", "tinker resume #N")` to the
  ending), and `paused` when another job or the person paused it ("Paused for job #N", "Paused for you": its
  ending says so, and its tracker's `doing`); otherwise it is `finished`, and its journal is deleted (its
  `calls #N` log is kept). A `running` or `paused` job whose process is gone (killed, the computer restarted)
  is read as `stopped`; `Jobs.lost()` gives those jobs. A process is known by its ID and the time it started
  (`alive(pid, began)`), so a later process given the same ID is not taken for it.
- **JOB-3** `journaled(request, ask)` outside a job asks. In a job, a request made before, word for word, is
  answered from the job's journal (log `journal #N`) in the order the answers were kept, and nothing is asked;
  any other request is asked, and its answer kept unless it failed (`keep`). `Ollama.chat` (but an unload),
  `Internet.search` and `Internet.fetch` go through it.
- **JOB-4** `today()` is the day the job that runs started (today outside a job): the prompts that say the date
  (learning's plan, research's frame) say the same on the day a job is resumed.
- **JOB-5** `Jobs.arguments`: `resume #N` runs job #N's arguments, as that job (its number, its journal);
  `resume` alone runs the job stopped last, or, with no job stopped, the folder's run; `resume @PROJECT` runs
  that project. `confirm` is never a job's resume: it always builds the plan that waits in its folder. An
  unknown job, one that runs in another process, and a finished or archived one are refused with why. A
  task's job is resumed as `resume @RUN` once its run is known (`Jobs.retarget`). A job that goes on
  (`resume #N`) starts its tracker with `resumed`, so its message comes to the bottom of the chat.
- **JOB-6** `tinker jobs [running|paused|stopped|finished|archived] [PAGE]` lists the jobs by page
  ([State](#state)): with no state, the running ones, then the paused ones, then the stopped ones. Any other
  words are an error.
- **JOB-7** `tinker stop #N` sends job #N `JOB_STOP_SIGNAL`, as Ctrl-C (one that neither runs nor is paused is
  an error); `tinker stop` stops the job that runs, whatever it is. In the conversation, `/stop #N` stops that
  job (the task started here, the exercise in its own message, or one elsewhere) and `/stop` the job that runs,
  and `/resume [#N]` starts `tinker resume #N` as a job (an exercise as the exercise). A paused job that is
  stopped no longer goes on by itself.
- **JOB-8** One job runs at a time, whatever kind it is. A job that starts (`Jobs.begin`) pauses every job that
  runs: each is marked `paused_by` it and told to stop (`JOB_STOP_SIGNAL`), and it waits, at most
  `JOB_PAUSE_GRACE_SECONDS` seconds, while they save themselves. A paused job keeps its process: it waits there
  (`go_on_when_idle`). No rule refuses a job because another runs: not the conversation's task, nor a second
  exercise.
- **JOB-9** A paused job goes on by itself, at once, as `resume #N` in its own process, as soon as Tinker is
  idle (`idle`: no job runs, and no request of the person's is being answered: `answering`, in the home's
  `activity`, kept by `paused_for_you`; a request of a process that is gone does not count). There is no quiet
  time to wait out. Of the paused jobs, the newest goes on first (`next_to_go_on`), so one at a time.
  `resume #N` of a paused job makes it go on at once (`Jobs.wake`), in the process where it waits: a paused
  job never runs in a second process.
- **JOB-10** A request the model answers right away, a chat or `command WHAT` (in the conversation, or
  `tinker chat` and `tinker command`), pauses the job that runs while it is answered (`paused_for_you`: paused
  by "you"). The request is marked as being answered before anything is paused, so the paused job does not go
  on the moment it has saved itself. The person is answered as soon as it has, and the job goes on as soon as
  the answer is written (a command line run once confirmed pauses it again while the model reads its output).
  A glance (**CMD-1**) pauses nothing, and a job asked for pauses it as **JOB-8** says.
- **JOB-11** `tinker clear jobs [#N...] [--yes]` archives those jobs (with no `#N`, every stopped and finished
  one), as a deletion (**SET-9**): each is kept with the state `archived` (and `archived_from`, the state it
  had), listed by `tinker jobs archived` and not without a state, and its journal and its `calls #N` log are
  deleted. An archived job is not resumed (**JOB-5**). An unknown job, a running or paused one, one archived
  already, and any word but `#N` are refused with why.
- **JOB-12** `calls_log()` is the one log of a job's model calls: `calls #N` for the job that runs, kept when
  it finishes and deleted when it is archived (**JOB-11**). Outside a job it keeps nothing. `learn`, `ask`,
  `teach`, `read`, `research` and `exercise` give it to their model and to `say` (`detail`, **MSG-5**);
  calibration (`model`) calls Ollama directly and keeps none.

## ledger (LED)

What is said in the chats, kept so that a chat is only a view of it.

- **LED-1** `Ledger` keeps each message by channel, chat and id: `kept` goes in `MESSAGE_KEPT_SECONDS`,
  `touched` moves that on (but a glance's, or the status'), `glance` in `GLANCE_KEPT_SECONDS` (or the seconds
  given), `lasting` never. `due` gives the messages whose time is up, `passing` all but
  the status, `glances` a chat's glances; `gone` forgets one.
- **LED-2** `shown(channel, chat, thing, ids, text, asked)` keeps a thing as it was shown last; with no thing,
  a new one is numbered, keeping what was asked. `thing_of(ids)` gives the thing messages show. `page(N)` lists
  the things by page ([State](#state)), with how many pages there are.

## prompts (PRM)

- **PRM-1** `Reply(raw).text` is the raw reply without `<think>…</think>`, stripped.
- **PRM-2** `Reply.json()` gives the JSON object in the reply, with prose around it allowed. Only when it
  does not parse, it closes brackets the reply left open: before a closing bracket further out, and at the
  end. No object is a `ValueError`. What cannot be mended raises the reply's own `JSONDecodeError`, so the
  caller asks again.
- **PRM-3** `Reply.files()` gives `(path, body)` for each `<<<FILE path>>> … <<<END>>>` block. The header may
  be `path=x`, `file: x`, `"x"`, or have no `>>>`. A placeholder path (`path`, `file` …) followed by the real
  path, on the header line or on the first line, takes that real path. A body that is only the format's
  placeholder is skipped. Stray format marks (`>>>`, `<<<…>>>`) at the ends of a body are dropped. One
  markdown fence around the whole body is dropped, except in a Markdown file.
- **PRM-4** `Reply.line_edits()` gives `(path, first, last, lines)` for each `<<<LINES path A-B>>>` block. The
  line-number prefixes (`12| `) are removed when every line has one.
- **PRM-5** `Reply.as_file(path)` turns a reply that has no FILE block but exactly one fenced block into that
  file. Any other reply stays as it is.
- **PRM-6** `Reply.files_ended(content)` is true when the last block has ended and nothing after it begins a
  block. `Reply.files_complete(content)`, which stops a stream early, also needs at least 300 chars after it
  (the model is running on). `Reply.wrote(paths)` is also true once every wanted path has its block.
- **PRM-7** `Reply.json_complete(content)` is true once the content ends with `}` and the object from its
  first `{` parses.
- **PRM-8** `cause()`, `test_wrong()`, `criterion()`, `corrected_criteria()` and `toolchain_line()` read their
  marker lines (`CAUSE:`, `TEST WRONG:`, `CRITERION:`, `CRITERION <test>:`, `TOOLCHAIN: {json}`).
  `named_paths()` gives each path named by a block, once, in order.
- **PRM-9** `Reply.spec_json()` keeps the rest of a spec, with `data` as text, when only its last part
  (`"data"`) is broken.
- **PRM-10** `Part.assemble(parts, limit)` lays parts out by `place`: `"first"`, then unplaced, then `"last"`,
  each group in its given order. Required parts are always included. An empty optional part is left out.
  Optional parts are added while they fit. A non-atomic optional part is shortened to the room left (if at
  least `SHORTEST_KEPT_CHARS` chars). An atomic one is whole or left out. When the required parts fit, the
  result fits the limit.
- **PRM-11** When the required parts do not fit, the longest prose part is shortened first, never below
  `MIN_TRIMMED_PROSE_CHARS`. A `head` part keeps its start, a `tail` part its end, with a `[...]` mark. Exact
  parts are never shortened. When nothing more can be shortened, `PromptTooLarge` names the exact parts. The
  shortened titles are added to `notes`.
- **PRM-12** A part is `exact` only when it is required, and `atomic` only when it is optional. `relaxed()` is
  the same part, but not exact. A part renders as `## title\ntext`, and its size is the rendered length + 2.
- **PRM-13** The example JSON in `SPEC_SYSTEM`, `FEATURE_SPEC_SYSTEM`, `TOOLCHAIN_SYSTEM` and `README_SYSTEM`
  parses. Its toolchain is a usable `Toolchain`. Every `{placeholder}` template (`BRIEF_SYSTEM`) formats.
- **PRM-14** Each command in the example toolchain is a real command line of its own tools. Tinker's own words
  never appear in it.
- **PRM-15** `LESSONS_SYSTEM` asks for general lessons only: never a project's names, files or data.

## workspace (WS)

- **WS-1** `Workspace.run(command)` runs one command in a POSIX shell in the project folder. Input comes from
  `/dev/null`. stdout and stderr are merged, and the project folder's path is removed from the output. It
  gives `(exit code, output)`. A command that times out gives `(None, output)`, and its whole process group is
  killed. An unsafe command is never run: it gives `(None, REFUSED_OUTPUT)`.
- **WS-2** These commands are unsafe: `sudo`; `rm -rf` of `/`, `~` or `..`; `mkfs`; `dd if=`; a download
  piped into a shell; a redirect into `/etc`, `/usr`, `/bin` or `/dev/sd*`. Ordinary build and test commands
  are not.
- **WS-3** `Workspace.resolve(path)` accepts a path relative to the project and inside it. It rejects with
  `ToolError`: an empty path, an absolute path, a path starting with `~`, a path that leaves the project, a
  path that is too long or has odd characters, and a write into `.tinker` or `.git`. The root is taken
  resolved, so a root reached through a symlink works the same.
- **WS-4** `write` makes the parent folders and ends a non-empty file with a newline. `read` of a missing file
  gives `""`. `read` and `exists` see only files inside the project: a path that leaves it reads as `""` and
  does not exist.
- **WS-5** `apply(reply, may_change)` writes each FILE block that `may_change(path, exists)` allows, and keeps
  a backup (`None` for a new file). It refuses with a note: a placeholder path, a path outside the project, a
  file shown only in part, and an empty body where empty is not allowed. `revert(change)` restores each backup
  and deletes the files the change created.
- **WS-6** A LINES block changes only a file shown as numbered lines, and only within the lines shown. Of two
  overlapping blocks, the earlier one in the reply is applied and the later one is dropped, with a note. A file
  shown in part takes no FILE block.
- **WS-7** `Change` is true when it changed a file. `files` lists the changed files, and `created` lists the
  new ones.
- **WS-8** `prepare_env()` makes `.env` from `.env.example` when there is none. Only the values that are
  obvious placeholders are blanked, and it says which settings to fill in. An existing `.env` is never
  changed. `is_placeholder` is true for an empty value, `<...>`, `your_...`, `..._here`, `changeme`,
  `replace-me`, `xxx`, `***`, `...` and `placeholder`. It is false for any other value, such as `none`,
  `null`, `PUT`, `todo` or `example_db`.
- **WS-9** `start(command)` starts a long-running command in the background, with its output in
  `.tinker/app-output.txt`. `stop(process)` ends it and everything it started. An unsafe command is not
  started.
- **WS-10** `TaughtCommands.teach(name, definition)` keeps an alias in the home's `commands`. A `source` in it
  becomes `.`. The definition is checked as POSIX shell. The name must be a shell function name. `"none"`
  forgets the command; forgetting one never taught ends with `SystemExit`. An empty definition ends with
  `SystemExit`.
- **WS-11** `TaughtCommands.all()` rejects entries that are not aliases or programs. `spelled_out` puts each
  alias's definition in its place, as whole words only. `prelude(command)` defines, as shell functions, the
  aliases the command uses. `Workspace.run` puts the prelude first, so a taught alias works in a project's
  commands.
- **WS-12** `Toolchain(data)` makes lists of `setup` and `use`. It leaves out the setup commands that write
  `.env` (kept in `env_commands_dropped`) and those that change the machine's own tools (kept in
  `machine_changing_commands`). A lint of `"none"` means the language has no linter: `lint` is `""`, and
  `to_dict()` gives `"none"` back. A toolchain is true only with both `test_all` and `test_one`.
- **WS-13** `shell(command)` runs the command after `activate`, when there is one. `merged(changes)` gives a
  toolchain with the changes: an empty command keeps the old one, and a list, even an empty one, replaces the
  old list. `problems()` names a missing `test_all`, a missing `test_one`, a missing lint, and a `test_one`
  with neither `{name}` nor `{file}`.
- **WS-14** `run_setup(ws)` runs the setup commands in order. A command that fails stops setup with
  `$ <command>  failed (exit code N)` and its output. A failure caused by the network is set aside as waiting,
  and the rest go on. A command that makes a new project in a subfolder stops setup. Once `activate` works,
  later commands run in that environment.
- **WS-15** `run_test(ws, ref)` gives `"passes"` only when the command exits 0 and ran a test. A command that
  ran no test, failed, or did not finish within `test_timeout` gives `"fails"` with why. `run_all` notes a
  suite that did not finish.
- **WS-16** `ran_no_test(output)` is true when the output says no test ran, and no part of it says tests ran.
  `reached_no_test` is true when the test command failed before reaching any test: exit code 126 or 127,
  refused, or no test file or name in its output. A timeout does not count.
- **WS-17** `TestRef("f::n")` has file `f` and name `n`, and a bare path has no name. `command(template)`
  fills in `{file}`, `{name}` and `{id}`. `present(ws)` means the file holds the name as a whole word.
  `excerpt` gives the test's own lines, after the file's opening lines. `remove` cuts the test, with its
  decorators, out of its file.
- **WS-18** `Output.key_line` gives the last exact error line, else the first failure-like line, else the last
  line. Banners are skipped. `signature` is the same for failures that differ only in numbers, times and
  addresses. `failure_text` keeps the end of the output, within the limit. `compact_lines` shortens very long
  lines to their two ends.
- **WS-19** `Output.misses_own_code` names the project's own module that a test could not import.
  `unloadable(output, path)` is true when a syntax or parse error is located in that file.
  `mentioned_in(output, paths)` lists the files the output names, the most named first. A file name inside
  another file's name does not count. `tool_failure` gives the key line without the project's path and
  numbers.
- **WS-20** `FileView(...)(limit)` gives the fixed parts, then each candidate file whole while it fits, then a
  numbered window (up to `windows` of them) around the line the output points at, then the tail parts. A
  how-to part comes with the windows. `shown` is the files shown whole plus the files shown in windows.
- **WS-21** A window holds the lines on both sides of the line it is about, as far as the room and the file
  allow: never only the lines from it downwards while lines above it fit.
- **WS-22** `online()` is true when one of the probe addresses accepts a connection.

## model (MOD)

- **MOD-1** `Models.settings(name)` is what calibration measured for a model, kept in the home's `models`
  under the model's name, or `None`.
- **MOD-2** `Models.selected()` ends with `SystemExit` when no model is in use, when the model in use was never
  measured, or when a measured value (`max_prompt_tokens`, `num_ctx`, `max_reply_tokens`, `fix_max_tokens`)
  is missing, below 1, or a boolean. Otherwise it gives `(name, settings)`.
- **MOD-3** `Models.installed(name)` ends with `SystemExit` when Ollama does not answer, or has no model of
  exactly that name. In that case it suggests close names. Otherwise it gives the name.
- **MOD-4** `Models.context_for(budget, reply, length)` is `budget + reply + 512`, rounded up to a multiple of
  1024, and at most the model's context length. `Models.reply_limits(reply)` sets the longest fix to
  `min(4096, reply)`.
- **MOD-5** `Models.remember(model)` keeps the chars per token the run learned, when it learned one that
  differs from the one kept.
- **MOD-6** `Ollama.is_oom` recognises "out of memory" and "insufficient memory". `is_crash` recognises the
  runner's crash messages (runner failed or terminated, panic, killed, segfault).
- **MOD-7** `Ollama.get_json(path)` gives the JSON Ollama answers with. It gives `None` when Ollama does not
  answer or answers with an HTTP error. With `errors=True`, an HTTP error gives the error's own JSON, or
  `{"error": "HTTP <code>"}`.
- **MOD-8** `Ollama.chat` streams a request with `model`, `messages`, `stream: true` and `keep_alive`. It
  sends `think` only when it is set, and `options` only when there are some. It gives the final chunk, with
  `message.content` and `message.thinking`. An HTTP error is an `OllamaError` flagged out of memory or crashed
  when it is either. An unreachable Ollama is an `OllamaError`.
- **MOD-9** `Stream` collects the reply and keeps the last `UNFINISHED_THOUGHT_KEPT_CHARS` chars of the
  thinking. An error chunk is an `OllamaError`: a reply error with what came before, unless it is out of
  memory or a crash. Once `done_when(content)` says the reply is complete, the stream ends within
  `LAST_CHUNK_WAIT_SECONDS` seconds, and the reply is the content as it was at that moment. While it goes, its
  live line says what Tinker works on (**MOD-24**), and it tells `say` its new chunks (`tokens`) and whether
  it is in its thinking phase (`thinking`). A reply whose last `LOOPING_REPEATED_LINES` finished
  lines are one non-blank line, again and again, is stopped there and is cut (`length`).
- **MOD-10** `Calibrator.shared(context)` splits the context the machine holds, with no fixed cap: the prompt
  budget (read limit) is `max(512, context // 3)`, and the longest reply (write limit) is
  `max(1024, context - budget)`. `safe_ratio()` is
  `STARTING_CHARS_PER_TOKEN` until measured, and then 97% of the measure, within [2, 4], to 2 decimals.
- **MOD-11** `Calibrator.capacity(target)` aims at the model's own context length (`UNKNOWN_CONTEXT_TOKENS`
  when it reports none) and gives `(target, None)` when the target reads. A GGUF model holds
  its whole context from the load, so reading `PROBE_SAMPLE_TOKENS` at a context shows that context reads. Any
  other model grows its memory as it reads: it reads `PROBE_SAMPLE_TOKENS` first, and the target reads when
  the loaded size, grown at the rate that read showed up to the target, and `RUNNER_OVERHEAD_BYTES` are within
  the GPU memory. When they are not, the capacity is the largest context, in whole 1024s, that the rate fits
  in the GPU memory, without reading it: `(that context, target)`. When a size is unknown, or the sample shows
  no growth, it reads the whole target. Otherwise it halves
  the gap until it is within `CAPACITY_SEARCH_PRECISION`, and gives `(largest that read, smallest that
  failed)`. Below the smallest usable context it ends with `SystemExit`.
- **MOD-12** `Calibrator.check_fit` ends with `SystemExit` when the weights and `RUNNER_OVERHEAD_BYTES` are
  more than the GPU memory. With either size unknown, it checks nothing.
- **MOD-13** `Calibrator.run()` measures the model and keeps, in the home's `models`, a prompt budget, a
  context, a longest reply, a longest fix, a think setting, chars per token, and its read and write speeds in
  tokens a second, and what its quality probe solved (**MOD-27**). Each calibration also appends those settings,
  with the model and when it ran, to the home's `calibrations` log (`Store.append`); `models` keeps only the
  latest. Its messages follow **MOD-28**.
- **MOD-14** A calibration probe that fails is recorded as crashed, and the runner gets time to free its
  memory before the next one. A GGUF model whose probe crashes is read again in pieces (`num_batch` 256), and
  keeps that option. When Ollama itself stops answering, calibration ends with `SystemExit`.
- **MOD-15** Calibration's first chat finds the think setting the model takes: `"high"`, else `true`, and none
  for a model that does not think. It asks for the context calibration aims for, so the first probe finds the
  model loaded, and asks again at 4096 when the model does not run there. A model that does not run ends
  calibration with `SystemExit`.
- **MOD-16** `Model` takes its limits from the measured settings. It builds at `build_think` only when the
  model thinks. Its chars per token stay within [2, 4]. `limit_chars(system)` is
  `tokens × PROMPT_BUDGET_SHARE × chars_per_token − len(system) − 40`.
- **MOD-17** `Model.ask_once` adds `NO_TOOL_CALLS` to the system prompt. It fits the user message to the
  prompt budget, gives an `Answer` with the reply's content as its value, and logs the exchange to its log.
  An `Answer` is one result of a model call: its `value`, `why` it is not usable (empty when it is) and the
  `thought` left unfinished. `needed(message)` gives the value, or ends with `SystemExit` naming the message
  and why.
- **MOD-18** A reply cut off while it is still thinking is asked again: first with twice the budget, up to the
  cap; then, if `unthinking_retry`, without thinking, with the thought so far to conclude. Otherwise the answer
  is empty, says why, and keeps the unfinished thought. A cut reply is finished, and kept,
  when its `done_when` says so or its blocks have all ended.
- **MOD-19** A failed request is tried again after what the failure calls for. Out of memory: a smaller prompt
  budget, up to `OOM_RETRIES` times. A stall: the model is reloaded, up to `STALL_RETRIES` times. A rejected
  think level: `true`, then no think setting; a rejected `build_think` falls back to the calibrated think
  setting (**MOD-15**), for the run. A reply error: one more try, then what came before it. A crash: the same
  call again; after a second crash near a limit, that limit is lowered for the run; after `CRASH_RETRIES`
  crashes it raises. Anything else is raised.
- **MOD-20** `Model.ask` tries a prompt that is too large again with every part relaxed. It raises
  `PromptTooLarge` only when even that does not fit.
- **MOD-21** `Model.ask_until_usable(…, read)` asks again when `read` rejects a reply, adding a note with the
  reason. Once the attempts run out, or when the prompt is too large even relaxed, its answer has no value
  and says why.
- **MOD-22** `learn_chars_per_token` keeps the lowest ratio of the last 20 prompts over 50 tokens, within
  [2, 4]. `learned_chars_per_token()` is `None` until 5 prompts have been seen.
- **MOD-23** Before each call, every other loaded model is unloaded, except an embedding model `Embeddings` used
  (`Ollama.embedders`). With the model kept loaded, it is unloaded once it grows past `max_growth` times its
  first size.
- **MOD-24** `Model` keeps each call's step and details in its log and never prints them. The person sees
  only a short line when it asks again, and its live line says in plain words what Tinker is working on (the
  step it is on, else "Working on it") and for how long ("Working on it… 1m 41s (101s)").
- **MOD-25** A quick call is one model chat outside a run: the chat's replies, its warm-up, the commands it
  proposes, the searches planned for a need (**KNW-25**), and a project's folder name. `Models.quick()` is `None` until a measured model is in
  use; otherwise it gives the model's name, its options (`num_ctx`, and `num_batch` when measured) and its think
  setting (`false` for a thinking model, else none). `Models.quick_reply(messages, tokens)` sends one chat with
  them, `keep_alive`, `num_predict` `tokens` and `QUICK_REPLY_TIMEOUT`, and gives the reply's content, stripped.
  An Ollama failure is its `OllamaError`. Nothing else outside `Model`, the quick call, an unload and
  calibration calls `Ollama.chat`.
- **MOD-26** `Models.choices()` gives the name of each model Ollama has that does not embed, in Ollama's order,
  with whether it was measured. It ends with `SystemExit` when Ollama does not answer, as `tinker model` does.
  `Models.embedding_choices()` gives the names of the ones that embed: their `capabilities` from Ollama's
  `/api/show` hold `embedding`, or Ollama gives none for them.
- **MOD-27** Calibration's quality probe asks every model the same tasks of `tinkerkit/quality_tasks.json`:
  8 short tasks in 4 tiers of 2, easy to hard (tier 1 a one-step code output or word problem; tier 4 a
  many-step trace, a subtle bug in a longer function or a many-step logic puzzle), each with an `id`, a `tier`,
  a `prompt` and one fixed `answer`. Tiers go in order; once a model solves neither task of a tier, the probe
  stops and the later tasks count as not solved. A task allows `QUALITY_ATTEMPTS` (3) attempts in one chat:
  after a wrong reply it adds `That answer is wrong. Check your work and answer again.` and asks again. A task
  has `QUALITY_TASK_SECONDS` (120) in all: each attempt is asked with the think setting calibration found and
  `num_predict` the seconds left times the measured write speed in tokens a second (`QUALITY_REPLY_TOKENS`,
  2048, when that speed is unknown); a task's seconds are the sum of its replies' `total_duration`, and no
  attempt starts with no second left. A reply cut off before an answer is a wrong attempt. A reply solves its
  task when it equals the answer after both are stripped, lowercased and freed of trailing punctuation, quotes
  and backticks; no code is run. The settings keep `quality_version` (`QUALITY_VERSION`, 2: a changed task
  set or scoring raises it), `quality_tasks` (every task's id), `solved` (a count) and `quality_results`, one
  `{id, solved, attempts, seconds}` per task asked.
- **MOD-28** Calibration speaks in plain words, with an emoji per line and exact figures in brackets; no line
  is long. Words are `tokens × chars_per_token / 6`, rounded. It says the title "Testing NAME on this machine
  (once per model, about a minute)", whether the weights fit ("NAME needs 18 GB (weights); this machine's GPU
  can use 24 GB"), each read probe ("Checking it can read about 6,000 words at once (8,192 tokens, about
  12s)…", then "Read it in 10s", "Too much for this machine (ERROR)" or "Read it in smaller pieces (num_batch
  256)"), "Solving 8 test tasks, easy to hard, up to 2 minutes each…" with ✅ or ❌ per task as `Task
  reason-15 (2 attempts, 34s)` (`1 attempt` in the singular), "Stopped after tier 3: neither task solved" when
  the probe stops, and "Solved 5 of 8", and ends with one
  multi-line message, so a channel shows all of it (**CNV-18**):
  ```
  ✅ NAME is ready
     📖 Reads about 225 words a second (300 tok/s)
     ✍️ Writes about 19 words a second (25.1 tok/s)
     📥 Takes requests up to about 8,000 words (10,922 tokens)
     📤 Replies up to about 16,000 words (21,846 tokens)
     🧠 Solved 5 of 8 test tasks (1 after a retry, ~40s an answer)
     💭 Thinks before it answers (think: high)
  ```
  An unmeasured speed is `?`. The brackets after the solved count are **MOD-30**'s retry and answer-time
  facts, left out when there are none.
- **MOD-29** `Models.rows()` gives `tinker model`'s list: a heading `General models` with a row for each model
  of `Models.choices()`, then `Embedding models` with one for each of `Models.embedding_choices()`, a group with
  no model left out. A row is the name, then facts in plain words, with no adjectives: a measured model
  `in use · writes ~19 words/s · reads ~225 words/s · solved 5 of 8 · 1 after a retry · ~40s an answer`
  (`in use ·` only for the model in use, words as **MOD-28** counts them, an unmeasured speed `?`, the solved
  facts as **MOD-30** says them; one whose `quality_version` is not `QUALITY_VERSION` never ran the current
  test tasks, and says `test tasks not run · tinker model recipe all runs them` in their place), one never measured
  `not measured yet · choosing it measures it (about a minute)`, an embedding model `in use · 0.3 GB` (its
  size; `in use ·` for the embedding model).
- **MOD-30** `Models.recipe()` gives the recipe lines, so the person sees what to pick, from the measured models
  that do not embed. Every measured model was asked the same tasks (**MOD-27**), so its facts compare as they
  are: its solved count, its retries (the solved tasks that took more than one attempt) and its answer time
  (the mean seconds of its solved tasks). They read `solved 5 of 8 · 1 after a retry · ~40s an answer`, the
  retries left out when none, the answer time when nothing was solved. The picks come from the models that
  solved at least one task: the smartest solved the most tasks (a tie: fewer retries, then the shorter answer
  time, then Ollama's order), so a slow model that solves 7 beats a quick one that solves 5; the fastest has
  the shortest answer time (a tie: more solved, then Ollama's order), so a correct answer counts, not a fast
  wrong one. The lines are
  `🧠 Smartest: NAME (solved 7 of 8 · ~80s an answer)` and `⚡ Fastest: NAME (~20s an answer · solved 5 of 8)`;
  when one model is both, the one line `🧠⚡ NAME is the smartest and the fastest (solved 7 of 8 · ~20s an answer)`.
  With no model that solved a task, the fastest writer of them all alone, as `⚡ Fastest: NAME (writes ~40 words/s)`.
  `tinker model`'s list shows the recipe above its rows when two or more models are measured.
- **MOD-31** `tinker model recipe` prints the recipe (**MOD-30**) under the heading
  `🍳 Model recipe, picked from 4 measured models`, with ` (2 not measured: tinker model recipe all measures them)`
  when models that do not embed were never measured or ran an older test. A measured model is one whose
  `quality_version` is `QUALITY_VERSION`, so each model counts once; one measured model is enough.
  With none measured, it prints `🍳 No model is measured yet: tinker model recipe all measures them`.
  `tinker model recipe all` first measures each of those models, one after another, changing no model in use; a
  failed measurement says so and the next one goes on. A model never calibrated is measured as `model calibrate
  NAME` does (**MOD-28**); one calibrated with an older test reruns only the quality probe (**MOD-27**) with its
  kept think setting, `num_ctx` and write speed, under the title "Testing NAME again on the current test
  tasks", keeps its other settings and ends with `✅ NAME is ready` and its 🧠 line. Then it prints the recipe.

## project (PRJ)

- **PRJ-1** `Runs.new_record` gives `{id, task, workdir, spec, created, status: "running"}`, with a
  6-hex-digit ID. `Runs.save` sets `updated`, keeps the record in the project's database, and keeps in the
  home's `projects` only a pointer: the run ID, the folder and the task.
- **PRJ-2** `Runs.find(name)` finds a project by its full run ID or by its folder's name, exactly, and never by
  part of either. No match ends with `SystemExit`, and so do two projects in folders of the same name (it
  lists their IDs). `Runs.entries()` gives each pointer, with the project's own record when its folder is
  still there.
- **PRJ-3** `Runs.workdir(name, folder)` gives the named project's folder, and its absence ends with
  `SystemExit`. Without a name, it gives the given folder, resolved, which must exist.
- **PRJ-4** `Runs.open_new` refuses a folder that holds a run, keeps the spec file's text in the project's
  database, saves a new record and gives the mode `"new"`. `Runs.open_existing` marks the run running. It
  gives the mode `"resume"` with the saved spec text, or `"change"` with the feature's spec file.
- **PRJ-5** `Runs.clear_folder` refuses the home folder, the filesystem root, and any folder that holds
  Tinker's home. It keeps the entry that holds the spec file. It empties the folder and forgets the pointers
  to it, as a deletion (**SET-9**).
- **PRJ-6** `Runs.remember(run, language)` keeps one line about the run in the home's `runs`: `{id, task,
  language, status, created, updated}`, at most `RUNS_KEPT`, the least recently updated dropped first.
- **PRJ-7** `RunChecks.before_start`. A new run needs an existing folder that holds no run, and an existing
  spec file when one is given. A saved run needs a valid phase in every cycle. `add`/`fix` need a project spec
  and no unfinished cycle. `amend` needs a cycle that waits for confirmation. `restart` reads the task and spec
  text before anything is deleted.
- **PRJ-8** `Project` keeps `criteria` (saved as `steps`), `cycles` and `files` in the project's database.
  `Project.load(folder)` reads them back. `open_cycle()` is the first cycle not done. `new_cycle` starts at the
  first phase of its kind. `new_criterion` numbers the criteria `C1, C2 …` (`B…` for a bug) from one counter.
  `cycle(id)` and `criterion(id)` give the one with that id, or `None`. `counts()` gives
  `(passing, parked, total)`. `test_names(file)` gives the names of a test file's tests, and
  `criterion_of_test(test)` the text of that test's criterion, or `""`. `save()` keeps the plan and writes
  `SPEC.md`.
- **PRJ-9** `criteria_of(path, steps)` gives the criteria whose test file uses the file, or whose text names
  the file (`name.py`, or `stem.` for code). A template's criteria are also those of the files that render it.
- **PRJ-10** `entry_files` gives the design's entry point first, then any file the run command names.
  `contracts(paths)` lists each file's contract lines. `register` adds new files without changing known ones.
  `dependencies(read)` gives the dependency files. `uses(path)` gives the files used, directly and one level
  down. `remove_file` deletes a file and drops it from the design.
- **PRJ-11** `SpecDocument` writes SPEC.md: the title, the run and language, a table of criteria with readable
  status, the commands (setup, activate and the rest, with aliases spelled out), each cycle (constraints,
  non-goals, interface check, external-service data), the design (files, roles, uses, contracts), and each
  criterion with its source and, unless it passes, its last error.
- **PRJ-12** `Brief.load` gives `""` for a change. It reuses the brief kept in the project's database. A spec
  that fits the spec prompt is used whole. A longer spec is condensed chunk by chunk with the model.
- **PRJ-13** `Outcome` starts as `stopped` with exit code 1. `done` and `awaiting` exit with 0, and `blocked`
  exits with 1.
- **PRJ-14** `Tracker` is a run's one report. It keeps the title, job number, run ID, folder, phase, steps
  done, passing spec points, what goes on now, what it works on (`working`), its tally, what it found, the
  latest message said, a plan that waits, the end, the seconds taken and when it went on (`resumed`). Each change is published at once in its job's `progress`
  (an exercise's build: in the exercise's job, as `build`), and appended to the project's `progress` log. A
  tracker outside a job publishes nothing.
- **PRJ-15** The steps are named by phase (`DOING_AND_DONE`: what each phase is doing, and when done). A step
  done says what came of it (`outcome`) after its words. The passing count is taken from the project on every
  save, for every criterion (`C…` and `B…`). A plan that waits is kept as data: its project, run, language,
  how to start it, its kind, what changed, its criteria, its non-goals and its folder. The end is kept as
  data, made by `ending`: its status, its headline, its details and the commands to go on.
- **PRJ-16** `Steps(names, tracker, only_if)` is one step pipeline: its steps are data, in order, and the last
  is the end (`done`). `enter(step)` moves the tracker to a step of its own, once in a row, and an unknown step
  is an error. `run(at, step, save, waits)` goes on from `at`, the step reached: it enters each step, runs it
  (`step(name)`), and saves the next one (`save(next)`), until the end (`done`). A step whose `only_if` says no
  is passed over, neither entered nor run. A step that gives False stops there, unsaved (`blocked`), and
  `waits()` stops it before the next step (`waiting`). The cycle's phases (**CLI-11**) and a practice project's
  steps (**EXR-4**) run this way. Learning (**LRN-7**) and reading (**RDG-12**) only enter their steps: a
  learn goes on through its journal, and a reading by its chapters' states. Both share `Tracked`: its
  `step(phase, said)` says the words and enters the step, and its `outcome(text)` gives the tracker what came of
  it; neither touches a tracker when there is none.

## fetcher (FET)

The web plugin's page fetcher, with the standard library only. Only `web` uses it.

- **FET-1** `Fetcher.fetch_all(urls)` gives a `Fetched` per address, in order: status, final address, bytes,
  text, content type, error and the seconds it took. The pages of one site go one after another over one kept-alive connection
  (`http.client`), the sites in parallel (`workers`); a connection the site closed is opened again once. One
  page failing never stops the others. It sends its User-Agent, asks for gzip, follows at most
  `MAX_REDIRECTS` redirects, and decodes by the header's charset, else the page's `<meta>` charset, else UTF-8.
  A page larger than `max_bytes` fails, saying so. An address the environment sends through a proxy is fetched
  with urllib, which honours it.

## passages (PAS)

Fetched pages as passages: main content, under headings, repeats dropped.

- **PAS-1** `is_html(text, kind)` is the one check for HTML: a content type that says html, or text that begins
  with `<!doctype html` or `<html`. `Cleaner.page` takes an HTML page's main content as blocks (`<main>`/`<article>` when it holds
  `MIN_USABLE_PAGE_CHARS`, else the body). It leaves out scripts, styles, navigation, headers, footers,
  asides, forms, `hidden`, `aria-hidden`, navigation roles, and every paragraph or list item that is at least
  `MENU_LINK_SHARE` links. A heading keeps its level, a list item is a block of its own (`item`), `<pre>` is a
  code block that keeps its layout, and the page's `<title>` is kept apart (`MainBlocks.parsed`). Broken HTML
  gives what was read before it broke. Plain text and Markdown are read by their lines (`#` headings, ```
  code). `Cleaner.pool(pages, known)` gathers blocks under their heading into passages of up to
  `MAX_PASSAGE_CHARS` (a code block whole), drops text a site repeats on several of its pages and a passage
  that repeats another or a known one (`NEAR_DUPLICATE_OVERLAP`), and numbers them on from the known ones
  (`P1`, `P2`, ...), each with its address and fetch time.

## web (WEB)

The web plugin, the only part that reaches web content. It has two calls, search and fetch, and it handles
every failure of either: no caller sees a backend, an HTTP status or an exception from the web.

- **WEB-1** `Internet()` searches with every engine of `search` (**WEB-2**): Mwmbl, Wikipedia and Wikibooks;
  no setting and no key choose. `Internet.state()` names them: `searches mwmbl, wikipedia, wikibooks`.
- **WEB-2** A `SearchEngine` has a `name` and `search(query, count)`, which gives at most `count` results, each
  `{title, url, content}` strings, and raises on any failure: the web's own error for an HTTP error, a timeout or no connection,
  and `SearchFailed` for an answer that is not JSON or has no result list. It uses the standard library only and sends a User-Agent naming
  Tinker and its repository (`https://github.com/Aslamkv/codeforge`), as Wikimedia asks. `Mwmbl` (name
  `mwmbl`) asks `https://api.mwmbl.org/api/v1/search/?s=<query>` with the query as plain keywords (it knows no
  operators); a title or extract given as a list of `{value}` segments is joined into one string. `MediaWiki`
  asks a wiki's `/w/api.php` search (`list=search`, `srlimit=count`, `maxlag=5`); an answer with an `error` is a
  failure, a snippet's HTML tags are dropped, and a page's address is `https://<host>/wiki/<title>` with spaces
  as `_`. `Wikipedia` (`wikipedia`, `en.wikipedia.org`) and `Wikibooks` (`wikibooks`, `en.wikibooks.org`) are
  MediaWikis.
- **WEB-3** `Internet.search(query, count)` asks every engine at once and adds no pause of its own. An engine
  that times out or answers HTTP 5xx is asked once more, at once; one engine failing never stops the others.
  It interleaves the engines' results by rank (each one's first, then each one's second ...), keeps those with
  an `http(s)` address, each address once, at most `count`, and gives `[]` when no engine answered.
  `Internet.search(query, count, engines)` asks only the engines named (every engine when none is).
  `Internet.search_all(searches, count)` runs several searches at once and gives each one's results, in the
  order asked. A search is a query (every engine, `count` results) or `{query, engines, count}`, as a model
  writes it: unknown engines are dropped (none left is every engine), its count is at most `count` (`count`
  when missing or not a positive number), an entry with no query is dropped, and at most
  `MAX_PLANNED_SEARCHES` run. At most `SEARCHES_PER_ENGINE` requests go to one engine at a time.
- **WEB-4** When no engine can be reached (no connection; a timeout is not that), the web is offline:
  `Internet` says so once (`Web: not reachable (why)`), and every later search and fetch it is asked gives
  nothing at once, making no request.
- **WEB-5** `Internet.fetch(urls, max_bytes)` reads the addresses with the `Fetcher` (**FET-1**) in parallel,
  at most `fetch_max_bytes` each unless `max_bytes` is given, and gives a `WebPage` per address, in order: its
  address (the final one, after redirects; a page that could not be read keeps the one asked), content type, bytes, blocks (**PAS-1**), title and `why` (empty unless it failed). An HTML page
  (`is_html`) and plain text, Markdown, JSON or XML give their blocks; `text` is them as text (`PageText`). Any
  other content keeps its bytes with no blocks. A page that could not be read has no bytes and no blocks, and
  its `why` says what happened; it is not asked again.
- **WEB-6** `RequestLog` appends every search and every fetched address to `Home.web_log()` as one JSON line
  `{at, kind, by, result, why, seconds, target}`: kind `search` or `fetch`, by the engine's name (`mwmbl`, `wikipedia`, `wikibooks`) or `fetcher`, result
  `ok` or `failed`. It counts them by kind, and `text()` gives the counts in one line.
- **WEB-7** `PageText` is a view over `Cleaner`'s blocks (**PAS-1**): a page's title, and its main content's
  blocks as text, a blank line between them. A heading becomes a `#` line of its level, list items become `- `
  lines one under another, and a code block keeps its layout.

## conversation (CNV)

The terminal and the chats are one conversation: the same commands, the same answers.

- **CNV-1** `Markup` renders `**bold**`, `*italic*`/`_italic_`, `~~struck~~`, `` `code` ``,
  `[links](https://…)`, fenced blocks, ` ```output ` blocks (folded), `- ` items (as `• `) and `#` headings: as
  plain text, as Telegram HTML (escaped), and as ANSI (plain when `NO_COLOR` is set or there is no terminal).
  `literal` escapes the marks. `of(template)` makes a message from a t-string: each value with its marks
  escaped, but `:md` keeps Markup as it is, `:code` makes a code span and `:block` a fenced block. Any other
  format is an error.
- **CNV-2** `Markup.columns(text)` joins the columns of a terminal table with ` | `. It leaves text that is not
  in columns as it is.
- **CNV-3** `pieces(text, size)` splits a message between lines into parts of at most `size` chars, and loses
  no text.
- **CNV-4** Helpers. `first_lines` cuts lines and counts
  the rest. `first_words` collapses whitespace and cuts. `text_of` decodes bytes. `after_word` drops the
  command word. `built_project(name)` gives the project named (`Runs.find`), or without a name the one built
  last.
- **CNV-5** In a conversation (the terminal and every chat) a command is sent with its `/`: `command_of(text)`
  gives the command a message starts with, by the command table, only when its first word is `/` and a
  command's name, whatever words follow (a command refuses words it does not take). A menu's form is taken
  too: `@bot` after the name (in a group) is dropped. A `/` word that names no command is answered
  `❓ No command /WORD: /help lists them`, and nothing else is done. A message without the `/` is a command
  too when it says one of the table's fixed phrases, with no model: `said_command(text)` gives it as
  `/PHRASE`, else `""`. A chat command's fixed phrases come from its rows' synopses: the name, then for each
  `|` alternative its words, where an optional `[...]` part is left out or, when it holds only lowercase
  words, gives one of them; an alternative with a required placeholder (an uppercase word, `@`, `#`, `"`)
  gives none, and options (`--yes`) are left out. So `clear chat`, `status`, `jobs running`,
  `model recipe all`, `exercise status` are phrases, and `task`, `fix`, `ask` none. The text matches when its
  words, lowercased, without punctuation and without the filler words `SAID_FILLER` (`the`, `my`, `please`,
  `can`, `you`, `tinker`, `show`, `see`, `what`, `is`, `it`, `now`, `usage`, …), are a phrase exactly:
  `Clear the chat, please!` is `/clear chat`, `show model recipe` is `/model recipe`, `What's the time now?`
  and `what time is it now?` are `/system time` and `ram usage` is `/system ram` (**SYS-3**), while `clear the chat and the history` stays a
  chat. The command's
  name may be left out when the words are the rest of just one phrase, of a command that does not delete:
  `show recipe` is `/model recipe`, while `chat` (`/clear chat` deletes) stays a chat. Such a message is handled as the `/PHRASE` sent, so a
  command that deletes asks first just the same. A message that answers a waiting question is not matched.
  The command line (`tinker help`) is not a conversation: its commands take no `/`.
- **CNV-6** `Channel.said` writes `tinker COMMAND` as `/COMMAND` in a conversation, in the terminal as in every
  chat, so what Tinker says to send can be sent as it is. Every command Tinker names in a conversation (a hint,
  the welcome, a usage, a question) has its `/`. `Channel.show` edits the
  thing's messages, sends parts that no longer fit, deletes the extra ones, adds the update time, and sends
  the message again at the bottom when it is no longer the newest.
- **CNV-7** `Channel.listened` gives only messages from allowed chats. It says a refusal once and gives
  nothing. `claim` lets only one process hold a channel's lock.
- **CNV-8** `ConsoleChannel` prints a thing's message as it grows: its title and each settled line once, as
  they come. The Tinkering line (what goes on now, **MSG-4**) and the lines under it stay out of the print.
  On a terminal with colour, they show above the prompt as one live block, the lines under the Tinkering line
  dim and indented, all redrawn in place; otherwise what goes on now prints as a line of its own each time it
  changes. When the thing is done, the live block clears and only the lines not yet printed follow. `/exit` or
  end of input closes it at once, asking nothing: the terminal reads it itself, and Tinker can be started
  again there. A plain `exit` is a chat. On a terminal, the prompt reads through `LineEditor` (**MSG-9**), and up and down
  on it go through the lines typed before, the last 1000 kept in the home's `prompt_history`, so they outlive
  a restart. Typing `/` there opens `CommandMenu` (**MSG-10**) under the prompt, narrowed as you type; up and
  down move in the menu while it is open. Piped input reads lines as before. `/model` or `/model calibrate` typed
  alone there prints the recipe (**MOD-30**), opens its select box on the prompt (**CLI-3**) and sends the
  command with the NAME chosen; a cancel sends nothing.
  Once it reads, all that Tinker says (`say`, a chat's message too) prints through it, above the prompt, and
  `live` draws in that same live block, its star turning and its glow moving on their own about eight times a second. While a job
  shows its progress there, the status that says what Tinker works on is left out of the terminal. A job's
  live line counts from when the job began (its tracker's `started`, less the time of earlier runs), so a
  Tinker opened later shows the same time.
  On a terminal with colour, the live block, the rule and the prompt follow what Tinker printed, as in Claude
  Code, and are never pinned to the screen's last row: they start right under the banner, and each line Tinker
  prints goes in right above the block and pushes the block, the rule and the prompt down a row. Once the
  prompt is on the last row, the lines above scroll up instead and older lines go to the scrollback. When the
  block grows or shrinks, the rule and the prompt move down or up with it. A message of any height
  prints above the block in pieces that fit, and the line you send shows again there; the prompt line, what
  you type on it and the menu under it are drawn again after each print, with the cursor where it was. When the window is resized, the block and the rule are drawn again
  at the new size right away, still right above the prompt and with what Tinker printed right above them, and
  they never spill onto the prompt row. The conversation opens in a framed banner that names Tinker, the model
  in use, the embedding model when one is set, the projects folder and the chats it listens on (each with its
  state, **CHN-12**), each on a line of its own; each chat opens with the same lines as a message of its own (the first in bold, the last in
  italics), kept by `clear chat` as the status is. When a message changes what the banner says (`model NAME`
  picks another model), the chats are cleared as `clear chat` does, the terminal's screen and scrollback are
  wiped, and the banner is drawn and sent anew, with the status below it. It draws a rule over the prompt, shows each line
  you send again highlighted, and marks each message it sends with a steel blue `●`. Piped input draws no
  rule and shows no line again; with `NO_COLOR` or no terminal, none of that is drawn.
- **CNV-9** `Desk.run` claims each chat channel. When another Tinker holds one, it stops if there is no
  terminal, and otherwise leaves that chat out. As each chat starts, what the last Tinker left in it is cleared
  as `clear chat` does, its banner from before is taken out, and the banner (**CNV-8**) is sent before the
  status. The terminal never waits on a chat: each chat starts in its own
  thread, and what Tinker does every few seconds while it listens (collecting the messages whose time is up,
  saying the status, taking up an exercise that runs) runs in a thread of its own, from the start, so the
  terminal reads at once and `/exit` or Ctrl-C ends Tinker at once. Tinker tells and never waits as it ends: it
  hands its last status (`not listening`) and the question to stop, to be taken out, to a sender that runs on
  its own (`tinker channel goodbye`, left out of the usage), and exits; the sender clears each chat as `clear
  chat` does (the history keeps every message), posts the status and takes the question out, and what happens
  to them is the chat's matter, not Tinker's. Once Tinker ends (`/exit` or Ctrl-C), it posts no status of its
  own: the goodbye's is the last, waiting for a status being posted to be done before it is handed on. Each chat has one status message: what Tinker works on (a job's title), never how far it got. Every
  time the status changes (Tinker starts listening, a job starts, waits for the person, ends, Tinker ends:
  `not listening`), the one before is taken out and the new one is sent at the bottom, with the message of the
  job that runs moved below it. A job says its status at once as it starts, before its own message, and the
  status before it comes back at once as it ends. While it listens it collects the messages whose time is up,
  every `EXPIRED_MESSAGES_SWEEP_SECONDS` seconds.
- **CNV-10** `/start` (what a chat app sends when a person first opens the bot) is answered with a welcome and
  a short, tldr-style guide for beginners in plain words: what Tinker does, then what to send, grouped by what
  a person does (build something, their projects, ask anything, while it works), each with one line on what
  it does, and `/help`. It is never taken as a chat or a command.
- **CNV-11** `Desk.answer` handles a command in one place: its own handler when the conversation has one
  (`Desk.handlers`, by command name), and otherwise by its action in the table (**CMD-1**): one that deletes
  asks first, a `job` starts a task, a `terminal` one says to run it in a terminal, and the rest run as
  `tinker`. `task WHAT` starts a task, and a bare `task` gets its usage. A
  run that starts a cycle is given `--confirm`, so its plan waits for the person. `status` alone gives what
  runs (`status @PROJECT` is that project's status, **CLI-3**). `stop`, `stop #N` and `resume [#N]` act on
  jobs (**JOB-7**). A command that deletes shows what it deletes and waits
  for `/confirm` (**SET-9**), but `clear chat`, which deletes no record (the history keeps it), is done at
  once. `/exit` in a chat asks first, as Tinker cannot be started again from there (the terminal's closes at
  once, **CNV-8**); the person's `/exit` and `/confirm` are taken out at once, and the question as Tinker
  ends (its status says it no longer listens). The preview of a deletion is what `tinker` printed (its stdout),
  and the plan that waits is kept without the lines on what to send next: the chat never filters `tinker`'s
  text.
- **CNV-12** Each chat has at most one question that waits for the person (a deletion, a chat's `/exit`, a
  command line, words a command needs), kept in one place by channel and chat: what its answer runs and what a
  cancel says. `Desk.settle` runs what waits for a `/confirm`. Any other message cancels it and says so; a question
  with nothing to say on a cancel (words a command needs) is dropped quietly. The
  plan that waits is not such a question: every chat and the terminal share it. `decide` builds or changes the plan that waits. For a new project, its first
  `confirm` asks for the folder's name.
- **CNV-13** A command that needs words, sent alone (as a chat app's menu sends it: `/fix`), asks for them
  (`ask_for`: what it does, what to send, an example) and takes the next message without a `/` as them
  (`fix MESSAGE`), even one that starts with a command's name. A command instead (sent with its `/`, as a file
  sent to read is) drops the question. A command whose words are optional (`status`) runs at once. `/model` and `/model calibrate` sent alone on a channel with buttons (Telegram) ask with
  `choose(chat, question, names)` instead: a button with the name of each model its select box (**CLI-3**)
  offers, the question holding the recipe and the rows (**MOD-29**, **MOD-30**); the next message (a tap sends the name) is the NAME, as above. With none to
  show, or on a channel without buttons, they run at once and list the models.
- **CNV-14** A glance (a command of kind `glance`, **CMD-1**) is answered with `glance`: the person's message
  goes at once, the answer in `GLANCE_KEPT_SECONDS`. `history [PAGE | #N]` lists the things shown by page
  ([State](#state)), with what was asked for each, or shows thing N again. `clear chat` takes every message out
  of the chats but the status, said in a note that goes in `DONE_NOTE_KEPT_SECONDS`. `clear history` is
  `tinker clear history` (**SET-10**): once confirmed, it forgets the history, clears the chat as `clear chat`
  does, and leaves nothing in the history, not even what it said.
- **CNV-15** `Desk.follow` follows a task by its tracker: its run ID, its folder (the title is named after it),
  and a plan that waits. Once the task ends, it reads the task's last state from its job.
- **CNV-16** `running(skip)` lists the trackers of the jobs that run or are paused, but for its own process and
  those it skips. A job whose process is gone is not among them (**JOB-2**).
- **CNV-17** `Progress(snapshot)` shows a run's tracker: its title with its job (`(Job #N)`, so the person can
  stop it at once), its steps done, the step it is on as the Tinkering line (**MSG-4**, its star one frame
  further each edit), what the model writes now, the passing
  spec points and the latest line (`text`); a one-line `brief`; how long it took (`took`); and its `ending`: the
  end's headline, details and next commands, or the plan that waits with its criteria and what to send next.
- **CNV-18** `Reporter` shows one run's tracker in one message per chat. It reads the tracker every
  `PROGRESS_EDIT_SECONDS` seconds (about 1–2) and edits the message when a line changed; when only the time
  or the tokens changed, it edits at most every `CLOCK_ONLY_EDIT_SECONDS` seconds. It ends with the run's ending (with
  its `text`: a research's answer). A command that keeps no tracker shows its latest printed line, and ends
  with its title and that line, without the Tinkering line or its clock. Indented lines printed right after
  a line count as part of it, so a multi-line message shows whole.
- **CNV-19** A task run from a terminal reports to the enabled chats (`Attached`). Stopped or paused
  (**JOB-1**), its last state is shown once it ends.
- **CNV-20** The chat asks the model only through the quick call (**MOD-25**); `Talk` and `Shell`
  read a JSON answer with `Reply.json()` (**PRM-2**), and take one that cannot be read as `{}`. `reply` keeps
  the last `CHAT_CONTEXT_MESSAGES` messages per chat, and it says plainly when no model is in use or the model
  failed. The chat's prompt (`CHAT_SYSTEM`) asks the model, when the person asks Tinker to do what one of the
  commands does, to reply with only that command line (`/clear chat` for `wipe the chat`), and when they ask
  what a `system` check tells (**SYS-1**), to reply with only that check's line (`/system battery` for
  `am I charged?`), where memory means Tinker's `memory` command and only RAM means `/system ram`, and
  one command per line when several are asked for (`/system time` and `/system date` for `time and date`);
  asked how to do it, it answers in words. A reply whose every line, with or without backticks, is one that
  `command_of` gives a command for is not shown. A line runs as sent when its words are a fixed phrase
  (**CNV-5**) or its command is a glance (**CNV-14**), since a glance changes nothing; a reply of several
  lines that all run as sent runs each in turn. A one-line reply that does not (more words:
  `/fix the login bug`) is offered, `👉 /fix the login bug — /confirm runs it`, and `/confirm` handles it as
  sent; any other reply is shown. This takes no extra model call.
- **CNV-21** While a chat or a command line pauses the job that runs (**JOB-10**, `Talk.pausing_jobs`), its
  note says what is paused for it, and that it goes on once they have their answer.
- **CNV-22** `Shell.propose` gives one command line to confirm, with a warning when it changes or deletes
  things. It gives no command for an empty, multi-line or failed reply. `take` hands the proposal over once.
  `run` runs it in the person's shell and shows the exit code and output.
- **CNV-23** `run [@PROJECT] ARGS [<<< INPUT]` runs a built project the way a person runs it: its run command,
  in its folder and environment, with the arguments quoted and the input given after `<<<`. The project is the
  one named, else the one built last. With no project built, or one with no run command, it says so.
- **CNV-24** `one_shot` says one of the conversation's commands once, in the terminal (`tinker chat`,
  `command`, `run`, `history`). Each needs words after it, except `run` and `history`. `chat` and
  `command` pause the job that runs (**JOB-10**). `command` runs nothing without a yes at the terminal.
- **CNV-25** `memory --graph` in a conversation makes the page (`tinker memory --graph`) and sends it with
  `send_file`, what tinker printed as its caption. The terminal opens the file in the browser and says where it
  is; a channel that cannot send files sends the caption with the file's path.

## channels (CHN)

The chat plugin: Telegram, and `tinker channel`.

- **CHN-1** `Channels` keeps each channel's settings in the home's `channels`, and its secrets apart in
  `channel_secrets`. The database is kept readable by its owner only: a file found readable by others is made
  private when it is opened. `make(name)` builds the channel of its kind, and an unknown name or kind ends with
  `SystemExit`. `enabled()` gives nothing when `TINKER_CHANNELS=off`.
- **CHN-2** `ChannelCommand.run` lists by default and rejects an unknown action. `list` shows each channel and
  its chats. `add` (from options or questions) checks the name, refuses a name taken, checks the token, and
  saves the channel disabled. `remove` drops the settings and the secrets, as a deletion (**SET-9**).
  `enable` and `disable` switch a channel.
- **CHN-3** `ask` gives the default when there is no terminal, or ends with `SystemExit` when it has no default.
  `ChannelCommand.check` ends with `SystemExit` on a refusal, and tries a dropped network again up to
  `FIRST_CALL_TRIES` times.
- **CHN-4** `pair` serves the chat whose sender says yes at the terminal. `start` needs an enabled channel (or
  the one named), and checks each one before listening.
- **CHN-5** `TelegramChannel.call` POSTs form fields (`None` left out) to `/bot<token>/<method>`. It gives the
  result of an ok answer, and a refusal is a `ChannelError`. A dropped connection is tried once more, and then
  it is an `OSError`. Too many requests (429) is waited out for the `retry_after` seconds Telegram gives, up to
  3 times, and then it is a `ChannelError`.
- **CHN-6** `receive` gives the text messages (and a file sent, as **CHN-10** says) and moves the offset past
  every update. A network failure gives no messages. `offer` lists every chat command in the bot's menu
  (`setMyCommands`, so typing `/` autofills them), each description cut to 256 characters; a failure is ignored.
- **CHN-7** `send` and `edit` post Telegram HTML, and fall back to plain text when the HTML is refused. An edit
  of unchanged text is harmless. A refused edit takes the message out and sends a new one in its place.
  `latest` tracks the newest message still in the chat. `ask_for(chat, question, hint)` sends the question with
  the reply box open (`force_reply`), the hint in it cut to 64 characters. `choose(chat, question, names)` sends
  the question with a keyboard of one button per name, one to a row, hidden after a tap (`one_time_keyboard`).
- **CHN-8** `send_file(chat, path, caption)` posts `sendDocument` as `multipart/form-data`: the chat, the file
  under its name, and the caption as Telegram HTML (at most 1024 chars). A failure sends the caption alone.
- **CHN-9** Every message of a chat served (Tinker's, and the person's) is kept in the ledger (**LED-1**); an
  edit moves its time on. `show` keeps the thing it shows, numbered, with the person's words it answers (their
  message within `ANSWER_WINDOW_SECONDS`). `glance` (**CNV-14**) also takes out the glance before it.
  `collect` takes out the messages whose time is up, and `clear` every message but the status. A message taken
  out without a network stays in the ledger; one Telegram refuses to take out is forgotten.
- **CHN-10** A file sent in an allowed chat is downloaded into the home (`Home.reading_file`) and taken as
  `read PATH`: the link Telegram gives a bot holds its token, so it is never passed on. A file over
  `TELEGRAM_MAX_DOWNLOAD_BYTES`, or one that cannot be downloaded, is said to the person, and nothing is read.
  A file from any other chat is ignored.
- **CHN-11** Every connection to Telegram (`TelegramChannel.connection`), in this Tinker and in the goodbye it
  hands on, gives each of the host's addresses `CONNECT_SECONDS` to connect and `CONNECT_SECONDS` for the TLS
  handshake after it, in the order the system lists them, so an IPv6 route whose connect or handshake hangs
  falls to IPv4 within that time; once the handshake is done, the call's own wait applies to its answer.
  Once a call Telegram answers, the chat's later connections try addresses of that call's family first, so
  its connections keep to one family and its state (**CHN-12**) does not flip between IPv4 and IPv6.
- **CHN-12** A chat channel's `state` is `connecting…` until its first call to Telegram ends, then
  `connected (IPv4)` or `connected (IPv6)` (the family of the address it connected to) after a call Telegram
  answers, and `unreachable: REASON` after a call the network fails. The terminal's banner (**CNV-8**) and
  the banner each chat opens with name the chats only (`chats Telegram`). When a chat's state changes after
  the banner, the terminal prints `Telegram · STATE` as a line of its own, dimmed; the banner is not drawn
  again for it.

## knowledge (KNW)

Lessons and proven knowledge, and what every kind of learning shares. Lessons, facts, findings and rules are
kept as principles (**PRI**); where a point below names them, it holds for principles.

- **KNW-1** `Library.slug`: "Test-driven development" gives `test-driven-development`, "C++" gives `cpp`, "C#"
  gives `csharp`, and "Python 3.14" gives `python-3-14` (at most 60 chars). `read(slug)` gives `None` for a
  topic that is missing or unreadable. `topics()` gives every readable topic.
- **KNW-2** A language has one identity, `language_key(name)`: its first word, without a version or what is
  in brackets ("Python 3.12" and "Python" give `python`, "JavaScript (Node.js)" gives `javascript`, "C++"
  gives `cpp`, and "C#" gives `csharp`). `Library.language(name)` finds a language learned under any version of
  its name. A language is learned and kept under its key, with its version apart. A language never seen starts
  empty.
- **KNW-3** `Library.items(topics)` gives one item per text, as `{topic, name, phase, text}`: the summary, the
  tools, what is installed, research findings, and each fact with its confidence. A broken topic is left out,
  with a note. `shown_in(item, phases)` sends machine items to the toolchain and config prompts, tools also to
  the tests prompt, findings to the spec and design prompts, and a fact to its own phase.
- **KNW-4** `add_lessons(new, confirmed)` confirms known lessons by number. It confirms instead of adding a
  lesson that is like a known one. It skips a lesson with no text, an unknown phase, or one that names the
  project (`names`). A new lesson is a candidate. A lesson goes to the language or to any language by its
  scope. A store keeps at most `MAX_LESSONS_PER_FILE`, and new lessons always stay. It gives how many were new.
  `lessons(phases)` gives the best evidenced first: the times seen plus the times helped, then the most
  recently seen.
- **KNW-5** Toolchain fixes. `known_fix` replays only a fix for exactly this failure, not tried yet. A replay
  that fails retires that fix at once. `remember_fix` keeps the last 20, one per failure. `similar_fix` needs a
  similarity of at least `TOOLCHAIN_HINT_SIMILARITY`. `remember_toolchain` keeps the toolchain without its
  `run` and `check`.
- **KNW-6** `missing_checks` names the language's learned tools that check code and the toolchain that worked
  does not run: a type checker it never names, a linter or formatter where it has no command.
  `toolchain_title` asks to add them. `unavailable_tools` names the learned tools not installed here that the
  toolchain neither installs nor runs, and that are not to be added.
- **KNW-7** Every lesson and every learned fact or finding has an ID and a status, `candidate` or `proven`.
  `prove(used)` makes the used candidates proven, and copies the used web facts into the language's knowledge
  as proven facts. `disprove(used, phases)` retires every used item shown in those phases' prompts, whether it
  was proven or not.
- **KNW-8** `facts(phases)` gives the language's proven facts for the prompts of those phases.
- **KNW-9** `Knowledge(language, run)` records each item's history as `{event, run, at}`, the last
  `EVENTS_KEPT_PER_ITEM`: a new lesson or fix is `born`, a lesson seen again is `confirmed`, every item a
  succeeding cycle used `helped` (counted in `helped`, proven before or not), a retired one `failed`, and one
  taught and checked `taught` (**TCH-7**). A replayed fix that works `helped`.
- **KNW-10** A retired item leaves the lessons, facts, findings or fixes it was among for the `retired` list
  beside them, with status `retired`, its `reason` and the run. That list keeps the last
  `RETIRED_KEPT_PER_LIST`. Nothing retired is shown in a prompt.
- **KNW-11** `Embeddings` keeps each text's vector by its key, per embedding model, in the home database. Only
  missing texts are asked for, in batches of `TEXTS_PER_REQUEST`, keeping the model loaded for `EMBED_KEEP_ALIVE` and in
  `Ollama.embedders`. A wrong reply gives `None`, and `failed` says why. `prune` keeps only the texts asked for. `cosine` is the cosine similarity, and 0 for a zero vector.
- **KNW-12** `Retriever.by_words` ranks the texts that hold the query's words with BM25, best first. `rank`
  fuses the word and meaning rankings and gives every index: the ones neither finds come last. When embeddings
  fail, the meaning search is turned off for the rest of the run.
- **KNW-13** `Learner.safe_commands` drops a command with a shell operator, a command that changes something,
  and an unsafe command.
- **KNW-14** `Learner.read_facts` keeps facts of a known phase, cut to `MAX_FACT_CHARS`, at most
  `FACTS_PER_CHUNK`. `add_facts` keeps a repeated fact once and adds the other page to its `also`.
  `relevant_chunks` gives the page's first chunk and then the ones best matching what is wanted (**KNW-12**),
  in page order. Facts about unreleased versions are dropped.
- **KNW-15** `look_at_machine(commands)` runs each command in an empty temporary folder and keeps its first
  line and version. A failing command means the tool is not installed.
- **KNW-16** `Researcher.read_frame` and `read_analysis` keep only well-formed questions, findings that cite
  known facts, contradictions of at least two facts, and gaps and checks within their limits. An analysis with
  no finding is a `ValueError`.
- **KNW-17** `Researcher.cross_check` folds repeats and adds support from other sites when the numbers agree.
  When they disagree, it returns the pair. `supported(stated, cited)` never rates a finding above what its
  facts allow: high only when at least half the cited facts are corroborated and none is disputed. `entry`
  sets each fact's confidence to disputed, corroborated or single.
- **KNW-18** `Researcher.research(topic)` frames the topic as questions, reads pages for each, cross-checks and
  analyses the facts, searches the gaps and checks in one more round, and keeps the topic as research: its
  questions, findings, contradictions, open gaps, and facts with their sources and confidence.
- **KNW-19** `Library.save` gives each research finding `cites`: the IDs of the facts it cites by number, so
  the link holds when a fact is retired.
- **KNW-20** A topic's playbook is its principles (**PRI**): nothing condenses facts into rules. What a learn
  finds a new project declares to get the newest version is a principle for every program (`when` "").
- **KNW-21** Learning a topic again changes its principles only by deltas (**PRI-3**): a principle the new
  passages contradict counts it, and nothing is retired for not being found again.
- **KNW-22** `Item(kind, id, topic)` names one knowledge item: a lesson (`lesson`) or a language's proven fact
  (`known`), with no topic; a toolchain fix (`fix`: its topic the language, its ID the failure); and a topic's
  `fact`, `finding` or `rule` (its topic the slug). `key` gives it as text, `kind:id` or `kind:topic:id`, and
  `Item.of(key)` reads that back (a failure may hold `:`). A run's used items, the graph's node IDs and
  teaching name items only this way. `retire(item, language, reason, run)` retires it from its own store: a
  lesson from the language's lessons or else any language's, a proven fact or fix from the language's, a
  topic's item from its topic; it says whether it was there.
- **KNW-23** `Researcher.answer(entry)` gives a research as text: its findings by question, each with its confidence
  and the pages of the facts it cites as `[n]`, then those pages as numbered sources. A research with no finding
  says so.
- **KNW-24** `Learner.web_pages(searches)` runs the searches at once (`Internet.search_all`), takes the results in turn, each address once, at
  most `TEACH_PAGES`, and fetches them in one `Internet.fetch`; a research round searches and fetches its pages the same
  way, all at once. A page the web could not read stands on its search snippet. Each page keeps
  its `relevant_chunks` for the searches (**KNW-14**), up to
  `TEACH_PAGE_CHARS`. A page with no text is left out.
- **KNW-25** Every prompt that writes searches (a research's frame, gaps and checks, a learn plan's
  follow-ups, and a teaching's checks) writes each as `{query, engines, count}`
  (**WEB-3**), and says what each engine is good for: `mwmbl` the whole web, plain keywords only; `wikipedia`
  overviews; `wikibooks` tutorials and manuals. Where code alone has a need (a learn's version and tool
  documentation searches), `SearchPlanner.plan(need, count, values)` turns it into searches with one quick call (**MOD-25**,
  no thinking) under that prompt, then fills the need's placeholders (`{topic}`, `{name}`, `{version}`) from
  `values`. With no quick model, a failed call or a reply with no search, the plan is the filled need as one
  search to every engine.
- **KNW-26** `SearchPlanner` keeps each plan in the home's `search plans`, keyed by its need before filling,
  lowercased with spaces collapsed, and plans a need found there with no call, so one plan serves every topic.
  A fallback plan is not kept. Plans stay until `tinker clear searches`.

## principles (PRI)

What every learning keeps: patterns found in what Tinker read or did, as principles in one store.

- **PRI-1** A `Principle` is `{id, when, do, because, exceptions, scope, phase, evidence, helped, harmed, status,
  history}`: when it applies (`when`, "" for always), what to do or expect (`do`), why (`because`), when it does
  not hold (`exceptions`), its scope (`any`, a language's key, a topic's slug or a reading), its phase (one of
  `FACT_PHASES`, `book` or `any`), and what supports it (`evidence`: each a passage, page, run, check or "your
  preference", with where it came from). `prompt_text` gives it compact for prompts (`when → do; unless …`),
  `english` as a sentence ("When …, …, because …, unless …"). Both name no evidence.
- **PRI-2** `Principles` keeps every principle in one home store (**STO**), whatever learned it: a learn, a
  reading, a research, a run's lessons (**FIN**), a practice's reflect (**EXR-4**) and teaching (**TCH-7**). A
  principle may hold across topics: its scope is `any` and its evidence names each topic. Toolchains, fixes and
  commands are kept apart, as before.
- **PRI-3** Principles change only by deltas, applied by code, never by rewriting them all: `add` (a new
  candidate, `born`), `strengthen(id, evidence)` (`confirmed`, adds the evidence), `contradict(id, evidence)`
  (`harmed` + 1, `contradicted`), `except(id, exception, evidence)` (adds the exception). A delta naming an
  unknown id, or an add with no `do` or no evidence, is dropped. An add like a kept principle in its scope
  (`SAME_FACT_SIMILARITY` on `when` and `do`) strengthens that one instead.
- **PRI-4** `Inducer.induce(passages, scope)` makes one call (`INDUCE_SYSTEM`) with numbered passages (source
  text, never facts made from it) and, numbered after them, the kept principles of that scope most about the
  passages (**PRI-6**, at most `INDUCE_KNOWN_SHOWN`). The reply is a list of deltas, each citing the passages it
  rests on by number; a delta citing no given passage is dropped, and the cited passages become its evidence.
  `do` is cut to `MAX_FACT_CHARS`, `when` to `MAX_RULE_WHEN_CHARS`. A reply with no list is not usable.
- **PRI-5** A principle is a candidate when added, and is proven, helped and retired as a lesson is (**KNW-7**,
  **KNW-9**, **KNW-10**). One with more `harmed` than evidence is retired, with the reason that it was
  contradicted. One no build can prove (phase `book`, or from a research) stays a candidate and is shown with
  its evidence.
- **PRI-6** A principle's `when` and `do` are embedded when it is added (**KNW-11**). `about(text, limit)` embeds
  only the text and ranks the principles not retired with `Retriever.rank` (**KNW-12**), best first.
  `for_phases(phases, language)` gives the principles of those phases scoped `any` or to that language, proven
  first, then by `helped`, for build prompts (**SES-3**).
- **PRI-7** `Item("principle", id)` names a principle (**KNW-22**), and `retire` retires it from the store. The
  graph shows each principle as a node of kind `principle`, with `cites` links to its evidence (**GRF**).
- **PRI-8** A home that keeps knowledge from before principles is migrated once, when it is first opened: each
  lesson, proven fact, topic fact, finding, rule and reading point becomes a principle with its ID, status,
  history and sources (a finding's and rule's `cites` as evidence), its `when` a rule's `when` or "", its `do`
  its text, its scope and phase from where it was kept. The old lists are then emptied and the home marked
  migrated. An item that cannot be read keeps its text as `do`, with a note.

## slots (SLT)

What learning a language or a tool must find out, as data, and each slot's passages.

- **SLT-1** Profiles are data. `PROFILES["language"]` is the slots installed, toolchain, fundamentals,
  this-version, limitations, best-use-cases and readiness; `PROFILES["tool"]` is installed, purpose,
  core-usage, this-version, pitfalls-and-limits, conventions, fit and readiness. Each `Slot` has an id, a
  question, a query hint, a passage count, a minimum, the section (fact phase) it fills and its source
  (`web`, `machine` for installed, `check` for readiness). `asked(plan, version)` is its question with the
  plan's hint; `search(plan, version)` is the plan's follow-up search for it, else its own query.
- **SLT-2** `Selector.select(slots, pool, plan, version)` gives each slot its passages from the one pool: its
  best `SLOT_CANDIDATE_PASSAGES` by the words of its question, the topic and the version are shortlisted, and
  only those are embedded; of them, only those that hold a keyword of its own search (`Slot.keywords`, without
  the topic) or are close to its question in meaning (`SLOT_RELEVANT_SIMILARITY`) count. They are ranked by
  words and meaning fused, taken by MMR so a near-repeat of a passage taken is passed over, at most the slot's
  count and within the digest's room. A passage may serve several slots.
- **SLT-3** `short_slots(profile, selected)` gives the web slots below their minimum, the emptiest first.
  Readiness, `missing(profile, facts, machine)`, gives installed when no version command ran and each web slot
  without a fact.

## learning (LRN)

`tinker learn TOPIC` and the recall phase of a run with `--web` learn a language or a tool by its slots
(`SlotLearner`), from the pages about the version installed that its searches find, through the steps of
**LRN-7**.
A domain is learned like a tool; only `tinker research` researches it (`Researcher`, **KNW-18**). Own-command
learning and run lessons are apart.

- **LRN-1** `DocCache` keeps the cleaned documentation pages a learn read in the home's `sources` as
  `slug@version` (`slug@latest` when no version is known). A learn reads them from there before the web, so
  learning a version again makes none of its documentation searches. A `latest` copy is searched again after
  `docs_latest_days`; a version's own is kept until `tinker clear docs`.
- **LRN-2** `read_plan` needs a kind (language, tool or domain) and a name. It keeps safe commands only
  (`MAX_VERSION_COMMANDS`), a language's tools (one per role of `TOOL_ROLES`, each with a safe version
  command), and hints and follow-ups for its profile's slots only; a tool's language is `used_with`.
  `read_digest` keeps a fact only when it cites a passage given and speaks of no unreleased version; each keeps
  its slot, the passages it cites, their pages (`source`, `also`), and a known phase (else the slot's
  section); at most `FACTS_PER_SLOT`.
- **LRN-3** Fail fast. No model call (plan, digest, condense or embedding; a search plan, **KNW-25**, may) runs until the web, or the
  documentation kept from before, has delivered usable pages. PREFLIGHT stops when there is no embedding model;
  GATE 1 when the search finds nothing, or nothing above `learn_cutoff` (when the web could not search, it has
  said why, **WEB-3**, **WEB-4**); GATE 2 when fewer than `learn_min_pages` pages give usable
  text (snippets never count). With a saved plan, GATE 2 counts the documentation's pages, and runs search 1
  when they are too few. Then it raises `LearnStopped` (a `SystemExit`: a non-zero exit) naming the cause, what
  was tried and the requests made, saying nothing was learned or saved and what was learned before is
  untouched, and the next step (after nothing above `learn_cutoff`: learn again later, or name the topic
  differently if it is ambiguous, never only more fully); nothing is written to Tinker's database.
- **LRN-4** A learn's version and tool documentation searches are needs planned by `SearchPlanner`
  (**KNW-25**). A first learn searches by `DOCS_SITE_SEARCH` (from the topic alone) on every engine, with no
  plan: a plan kept for every topic cannot suit each one, so no one engine's blind spot (a one-letter name, say)
  can leave the search with nothing about the topic. It picks the results whose
  title and snippet score above `learn_cutoff` (BM25), each address once, at most `learn_pages`, and fetches
  them in one `Internet.fetch`. Only then does the plan call run (at the model's own think setting); for a
  language it names each tool's version command. A saved plan is reused with no search and no call. The version
  commands (each run once) give the version installed. That version's documentation is found by
  `DOCS_VERSION_SEARCH` (the topic's name, its version and `documentation`), its results picked as above, and
  a version that is one implementation's (a tool named otherwise than the topic, `sqlite3` for SQL) searches
  the topic's own documentation, by its name alone. Among the results picked, reference pages come before
  news, release notes, changelogs and downloads; those serve only the slot of what this version changes. And
  each of a language's tools by its own `TOOL_DOCS_SEARCH` (its own version, at most
  `tool_docs_pages` pages), the tools' searches all at once; their pages join the pool, and are kept (**LRN-1**). Each slot's passages are induced into principles
  (**PRI-4**, scoped to the topic) at `learn_think`, and the topic is kept only after that succeeds, under the language's key or the topic's slug, with its plan, machine, documentation versions,
  sources, cited passages, status (`complete` or `incomplete`), missing slots and request counts; then
  indexed. A summary prints the slots filled and missing, what is installed (each machine command and the
  version it found), the documentation, the pages by source, the facts and the requests by kind.
- **LRN-5** After the digest, each slot left without facts gets the plan's follow-up search, at most
  `learn_searches` follow-ups in all; its pages join the pool, and that slot alone is selected and digested
  again. When a follow-up fails, the learn goes on, marked `incomplete`, with a warning and a note.
- **LRN-6** A topic kept as research is researched again by `tinker research`, and kept as it is by a learn.
  Any other topic is planned by its slots first: a plan that says it is a domain hands it to `Researcher` for
  `tinker research`, and a learn learns it like a tool.
- **LRN-7** Learning's steps (`LEARN_STEPS`, **PRJ-16**) are, in order: search, plan, machine, documentation,
  digest, follow-up, condense and keep (a domain's: frame and investigate). A saved plan skips search, unless
  GATE 2 finds the documentation kept too few (**LRN-3**); PREFLIGHT, GATE 1 and GATE 2 (**LRN-3**) are checks
  within them. A learn is not saved step by step: stopped, it goes on through its job's journal (**JOB-3**),
  so nothing done is asked again. Given a tracker, learning moves it through its steps
  (`Tracker.DOING_AND_DONE`), each once in a row; without one (a run's recall phase), the run's own steps are
  left alone. The summary is kept as data too (`recap`: answered, still open, documentation, pages, facts
  kept, web requests), and a `LearnStopped` carries why it stopped and what to do next (`details`).

## graph (GRF)

What Tinker knows as one graph, and the page that shows it.

- **GRF-1** `Graph.build()` makes one node per thing Tinker knows: each language (one for its knowledge and
  what was learned of it), any language, each topic learned, the person's commands, and each lesson, fact,
  finding, rule, toolchain, fix, command and retired item. A node is
  `{id, kind, label, text, status, group, details, sources, day}`; `group` is the language or topic it belongs
  to. A fact's details name its slot, and a topic's details name the slots it is missing. An item's `day`
  (`YYYY-MM-DD`, or empty) is when it was first kept: its first history event, else its `added` or saved day,
  else the day its topic was learned. A node's `id` comes from what it is, never from where it is in a list: a
  retired item's is `retired:` and the key it had before it was retired, so it stays the same while the item
  is kept.
- **GRF-2** Links: `belongs_to` from an item to its language, topic, any language or the person's commands;
  `cites` from a finding to its facts and from a language's rule to its facts and lessons; `became` from a web
  fact to the language's proven fact it was copied into; and `taught_by`, `confirmed_in`, `helped_in` and
  `failed_in` from an item to the runs in its history. A run is a node with its line from `runs`, or marked as
  having none.
- **GRF-3** `similar` links join lessons by meaning (cosine at least `SIMILAR_LESSON_MEANING`) when the
  embedding model gives their embeddings, else by words (at least `SIMILAR_LESSON_WORDS`). `similar_by` says
  which, and why words were used.
- **GRF-4** `GraphPage.html(graph)` is one self-contained page: the graph as JSON inside it, and no script,
  style, font or image from elsewhere. It holds a plain list of every node by group (readable without
  scripts), escapes every text, and draws the graph when scripts run.
- **GRF-5** `tinker memory --graph` writes the page to `Home.graph_page()`, prints what it holds (the counts,
  and how lessons were found similar), and opens it in the browser only when printing to a terminal.
- **GRF-6** `MemoryBrowser` shows memory from one `Graph.build()` (without embeddings), never from the stores
  themselves, so the browser and the page always show the same things. It deletes nothing and changes nothing;
  `tinker teach` corrects an item and `tinker clear PART` forgets a part (**SET-10**). Every list comes a page
  at a time ([State](#state)), and every view is a glance (**CMD-1**).
- **GRF-7** An item's short ID is the first `MEMORY_ID_CHARS` (6) hex digits of the SHA-1 of its node `id`, made
  longer only when two items share it, so it names the same item for as long as it is kept, whatever is learned
  or retired around it. `memory show ID` takes any prefix of at least `MEMORY_ID_CHARS` digits: one that matches
  no item, or more than one (listed), is refused with why. Hubs and runs have no short ID.
- **GRF-8** `tinker memory [PAGE]` lists the parts of memory: each language, any language, each topic and the
  person's commands, one line each with the name to see it by (its group), how many of each kind of item it
  holds and how many of them are proven, and for a language how many projects built it. Then how many items are
  retired, and what to do next: `memory NAME`, `memory show ID`, `memory find WORDS`, `memory recent`,
  `memory --graph`, `tinker ask`, `tinker teach` and `tinker clear PART`. With nothing learned, it says how to
  start.
- **GRF-9** `tinker memory NAME [KIND] [PAGE]` shows one part, named by its group or its label (case does not
  matter): its details (summary, when learned, the slots missing, projects built), then its items, each with its
  short ID, its status (✅ proven, 🌱 not yet), its kind and its first line, in the graph's order. Retired items
  are left out but counted, and the `retired` kind lists them alone. `KIND` (`toolchain`, `facts`, `findings`,
  `rules`, `fixes`, `lessons`, `commands` or `retired`, singular or plural) keeps that kind; without `NAME` it
  lists that kind across memory. A last word that is a number is the page, a kind word is always the kind, and
  the other words are the name. An unknown name is refused, saying `tinker memory` lists the parts.
- **GRF-10** `tinker memory show ID` shows one item whole: its kind, status, part (and `memory NAME` to see it),
  day, text, details and sources; what it cites and what cites it, what it became or came from, the lessons
  similar to it (each with its short ID and first line); and the runs that taught, confirmed, helped or failed
  it (`@ID` and the run's task, once per run and verb, with how many times when more than once). It ends with
  how to correct it (`tinker teach`).
- **GRF-11** `tinker memory find WORDS` lists the items whose text or label holds every word (case does not
  matter), retired ones too (marked ♻️), each as **GRF-9** shows it with its part. It shows at most
  `MEMORY_FOUND_SHOWN` (20) and says how many more there are; with none found, it says so.
- **GRF-12** `tinker memory recent [PAGE]` lists the items that are not retired by `day`, newest first, each
  with its day and its part; an item with no day is left out.

## reading (RDG)

`tinker read FILE | URL` reads what the person gives: a book, an article, a document. Its whole text is stored
first (no model), numbered on the reading list, so it is never sent twice; then every chapter is learned from,
the core ones first, and saved as soon as it is read. What it teaches is a topic of the library (kind
`reading`, slug `read-N`); its text stays for ask, teach and a focused read again (`read #N "WHAT"`).

- **RDG-1** `book_bytes(source)` reads a file, or downloads a web address with `Internet.fetch` (its
  bytes as they are, **WEB-5**), at most `READ_MAX_BYTES`; a missing file, a failed download or a larger one is
  an error that says so (the page's `why`).
- **RDG-2** `opened(data, name)` reads a source by what its bytes hold, never by its name: a zip is an EPUB,
  other text is Markdown or plain text (its title: a lone top heading, else the file's name). A PDF is
  refused, saying to convert it to EPUB first (Calibre); a web page is refused, saying a share link needs its
  download link; any other binary file is refused.
- **RDG-3** An EPUB is read in its spine's order. Its contents (EPUB 3's navigation document, else EPUB 2's
  NCX) mark where each chapter starts (an entry's fragment, else its document's start), at the shallowest
  depth that gives `READ_MIN_CHAPTERS` chapters (else the deepest); what comes before the first is the opening
  pages. Its title and author come from its metadata. An EPUB with no usable contents is split at its
  headings.
- **RDG-4** A Markdown text is split at its headings, at the depth `chapter_depth` gives (a lone top heading
  is the title, not a chapter). A plain text is split at its chapter lines ("Chapter 3", "Preface"), else into
  parts of `READ_WINDOW_CHARS`. A piece that holds no more than its heading is no chapter.
- **RDG-5** `kind_of(title)` gives a chapter's kind: `index`, `raw` (front or back matter: contents,
  copyright, acknowledgements ...), `front` (foreword, preface, introduction ...: what the source says about
  itself) or `body`. `index_terms(book)` gives the index's terms with how many pages each refers to, the most
  first, at most `READ_INDEX_TERMS`.
- **RDG-6** `stored(source)` stores the source's whole text before anything is read: on the reading list
  (`Readings`, the home's `readings`) as the next number, with its title, author, source, the fingerprint of
  its text, the day it was added and each chapter with its text, kind and state (`unread`; `raw` for an index
  and front or back matter: kept, never read). A text stored before is that one: it says its number, and
  nothing else is done.
- **RDG-7** The survey, made once per reading and kept with it, sees the contents (each chapter's kind and
  length), the front chapters and the index terms. `read_survey` needs a summary; at most `READ_KEY_TERMS`
  terms; `software` is true only when the reply says so; each chapter is `core`, `supporting` or `other` (left
  out: other). It orders the reading and never skips a chapter: `in_order` gives the core ones, then the
  supporting, then the others, each in the source's order.
- **RDG-8** `Reader.read(N)` reads every unread chapter in that order, each in parts that fit the prompt
  (`READ_CHAPTER_TEXT_SHARE`), one call per part, each shown what was learned from the reading before (the
  latest kept when the prompt is full). Each chapter is saved as soon as it is read: its new facts (one
  already kept is not kept again) join the topic, each with its chapter, and it is marked read; so a reading
  stopped goes on with the chapters not read yet. With every chapter read, it says so and asks nothing.
- **RDG-9** `read #N "WHAT"` reads again the chapters WHAT names ("chapter 7"), then those most about it by its
  words, `READ_FOCUS_CHAPTERS` in all, never a raw one: each shows its `excerpt`, its `relevant_chunks` for WHAT
  (**KNW-14**, `READ_FOCUS_CHUNKS` parts), and the prompt says what the person asks to look for. What it finds
  that is new joins the topic.
- **RDG-10** Each chapter part is induced into principles (**PRI-4**, scoped to the reading), at most
  `READ_FACTS_PER_CHAPTER` deltas a chapter. A principle from a source about building software keeps its phase
  when it is one of `FACT_PHASES`; every other has the phase `book`, which no build is shown.
- **RDG-11** `Readings.about(text, items)` gives the readings the text names by title, then those whose items
  were shown. `passages(numbers, query)` gives their passages (`MAX_PASSAGE_CHARS`) most about the query by its
  words, at most `BOOK_PASSAGES_SHOWN`, each with its title and chapter.
- **RDG-12** `tinker read FILE | URL` stores the source, then learns from it as `read #N`: its job's arguments
  become `read #N` (`Jobs.retarget`), so a stopped or paused reading goes on with what is not read, and nothing
  is fetched again. `read #N ["WHAT"]` needs a measured model and a known reading. Its tracker moves through its
  steps (`READ_STEPS`, **PRJ-16**): survey, chapters (and condense), and ends with what was read now, the
  chapters read, what is kept, whether builds use it, and what to do next.
- **RDG-13** `read list [PAGE]` shows the readings by page ([State](#state)), each with how many chapters are
  read and the facts kept; `read show #N` its source, summary and each chapter with its state and facts. Both
  are glances (**CMD-1**). `clear read [#N...]` deletes those readings (every one with no number): their text
  and their topic, as a deletion (**SET-9**).

## teaching (TCH)

`tinker ask QUESTION` answers from what Tinker keeps. `tinker teach WHAT` corrects it, and nothing changes
until what was taught is checked: the model turns it into claims and the change it asks for, the claims are
checked on this machine, on the web and in what Tinker read, the model weighs that evidence alone, and only
then is anything changed.

- **TCH-1** `Memory(embeddings).items` are the memory graph's toolchains, fixes, principles (in `english`)
  and commands, without the retired. `about(text)` gives at most `MEMORY_ITEMS_SHOWN` of them, the most
  related first: first the items whose kind and language (or topic) the text names. A language's toolchain
  counts as named with its language, however the text calls it ("toolset"). When the text names a topic, an
  item of a topic it does not name comes after them, and only when its meaning is at least
  `RECALL_OTHER_TOPIC_SIMILARITY` like the text's: a shared word alone ("NULL") never brings it. A second `about` embeds only its text: the
  items' embeddings are kept.
- **TCH-2** `Asker.answer(question)` gives the model at most `ASK_ITEMS_SHOWN` items (the first of `about`) and the
  passages numbered, and the version installed here of each topic shown (its machine's version), of no other
  topic: only the commands that found one, and a topic with none says it is not installed. The model replies JSON: `answer`, and
  `uses`, the numbers it rests on. The answer comes from them first, says things as they are (never what Tinker
  keeps or notes) and names the version when it depends on it. Tinker marks it, never the model: an answer with
  no number it was given in `uses` is marked as not from what Tinker learned. With nothing kept and no passage,
  the model is still asked, and its answer is marked. It says the answer alone: no steps and no sources, then
  the time taken (a terminal shows one status line while it thinks). It changes nothing but **TCH-11**.
- **TCH-3** An ask also sees the passages most about the question from each passage source: the readings
  (`Readings`; a reading named by its title or whose items are shown) and the documentation kept for the topics
  shown (**LRN-1**, any version), so it answers from the source's own words. Each source shortlists its
  passages by words first (**KNW-12**) and ranks only the shortlist by meaning; no source embeds all its pages.
  A passage whose text is one already given (the same page at another address) is given once. The ask gets at most
  `ASK_PASSAGES_SHOWN` passages in all, taken in turn from each source's best.
- **TCH-4** `read_plan` keeps at most `TEACH_CLAIMS` claims, each a fact or a preference (the person's own
  choice). A preference is only what the person marks with the word prefer: a teaching without it (`marked`)
  has facts alone, whatever the model says. It keeps at most `TEACH_CHECK_COMMANDS` commands and
  `TEACH_CHECK_SEARCHES` searches, and the changes: each rests on the claims it names and retires items shown,
  replaces toolchain commands of `TEACHABLE_TOOLCHAIN_KEYS`, keeps a lesson with a known phase or keeps a fact
  in place of an item shown (**TCH-8**; any other is dropped). Setup given as one line is split at `&&` into
  its commands; any other command given as a list is joined with `&&`. A plan with no claim, facts that nothing
  checks (no command, no search, and no reading it is about), no change, a toolchain key in two changes, or a
  toolchain key or value it cannot use is a `ValueError` (the model is asked again with why).
- **TCH-5** `Teacher.teach` checks the facts first. It runs only the commands that show what is installed
  (each other one is skipped, and said; a tool not found here may be in a project's own environment), and
  reads the pages the searches find (**KNW-24**). A preference is honored, never checked: with only preferences, nothing is
  run or searched and the judge is not asked. Facts with no evidence are unsure, and the judge is not asked.
- **TCH-6** `Verdict.read` needs a verdict for every fact (`holds`, `wrong` or `unsure`; one that holds with
  no evidence it can cite is unsure), and whether each change a fact is for follows. Each change is made on
  its own: when every claim it rests on holds or is a preference, and it follows. Each other one is said with
  why. When no change is made, nothing changes.
- **TCH-7** A change that is made retires the items it shows wrong (the reason names what was taught),
  replaces the commands of the language's toolchain (only when it has one, and the result is usable), and
  keeps its lesson (or its fact, **TCH-8**) as proven, with a `taught` event and its sources: the evidence its
  facts cite, and "your preference" for a preference. A lesson like a known one proves that one instead of
  being added.
- **TCH-8** A teaching about a reading is checked against its text: its passages most about each fact are
  evidence, and a plan needs no command or search for its facts. A change may keep a `fact` in place of an
  item shown (`about`): that item is retired, and the fact kept in its topic as proven
  (`Library.taught_fact`), with its phase, chapter and section, a `taught` event and its sources.
- **TCH-9** Both commands report through a tracker.
- **TCH-10** The first ask call may propose one check (its kind, code and what it shows). Tinker runs it
  (**SBX**) and a second call answers with what it printed ("I ran …, it printed …"); with no check there is one
  call. The second call goes on the first one's conversation: the same system prompt and messages, the first
  reply, then what the check printed, so the model reuses what it already read. A `sql` check runs in the
  standard library's SQLite in memory, statement by statement however they sit on lines, with attaching
  databases and loading extensions refused. A `command` check runs its code from a new empty folder with a tool learned and found on
  this machine; a tool with a package manager or installer anywhere in it (pip, npm, brew, apt, gem,
  `python3 -m pip` and the like) or an install word right after its program (`cargo install`, `go get`,
  `poetry add`) is refused. A check fails when it does not run, runs out of time, or one of its `sql` statements
  errors; a `command` check's exit code is part of what it printed. A refused or failed check is told to the model with why, once, on the same
  conversation, and the check it gives back runs instead. When that one is refused or fails too, or none is
  given, it is said, and the first answer stands.
- **TCH-11** An answer a check ran and confirmed is kept in its topic as a candidate fact, with a `checked`
  event and its sources (the check and what it printed). One like a fact the topic keeps (`SAME_FACT_SIMILARITY`)
  adds its sources and a `checked` event to that fact instead, whose status stays. `tinker teach` confirms or corrects it (**TCH-7**).

## sandbox (SBX)

A check from an ask runs only in a sandbox. Nothing is ever installed.

- **SBX-1** `Sandbox.here()` picks the backend once, by platform: `MacSandbox` on macOS, `LinuxSandbox` on Linux,
  `NoSandbox` anywhere else, or when the backend's probe fails. No other part asks which platform it runs on.
- **SBX-2** `Sandbox.run(command, folder, timeout)` runs from a new empty temporary folder with a timeout and
  limits on CPU time, file size and processes. It reaches no network and writes only inside that folder.
- **SBX-3** `MacSandbox` runs the command under `sandbox-exec` with a profile that denies the network and every
  write outside the folder. `LinuxSandbox` cuts the network with a new user and network namespace and allows
  writes only in the folder with Landlock (and no new privileges). Both let the command write to `/dev/null`.
- **SBX-4** `NoSandbox` fails closed: it runs nothing and says why.

## exercising (EXR)

`tinker exercise LANGUAGE` practises a language on its own, to write better code faster: practice projects one
after another, each built and given a feature, measured, and turned into lessons, until the person stops it.
It is a job like any other, so the person always comes first.

- **EXR-1** `identified(model, words)` gives a learned language's name with no call. Otherwise one call says
  whether the words name one language, could be several (`ambiguous`, with the likely ones), or none
  (`other`, with why). For anything but one language, it says so and ends (`SystemExit`, "Nothing done"): no
  build starts, no exercise is kept, and no folder is made. `read_verdict` needs a known verdict, and a
  language needs its name.
- **EXR-2** `knows(name)`: a language Tinker has not learned is learned first (`tinker learn`, as a build of
  the exercise). A language whose learned machine commands all failed is not installed here: it says so and
  ends before anything is practised.
- **EXR-3** `read_choice` needs a task and a feature, rejects a task like an earlier project's
  (`SAME_PRACTICE_TASK_SIMILARITY`), adds the language to a task that does not name it, and makes a folder name
  from the task when the one given does not fit. `free_folder` gives a folder of the exercise folder
  (`EXERCISE_FOLDER` in the projects folder) that no project has yet.
- **EXR-4** `practise` goes through a practice project's steps, `build → feature → measure → reflect → done`,
  saving each as it ends, from the step it reached (a new project is chosen only when none is open). `build`
  runs `tinker task TASK --workdir FOLDER`, and `tinker resume --workdir FOLDER` once the folder keeps a run.
  `feature` runs `tinker add FEATURE`, and resume once its cycle exists, only for a project that built
  (`done`). `reflect` makes one call, keeps its lessons through `add_lessons` (**KNW-4**), and keeps its
  `focus` for the next choice.
- **EXR-5** `measured(folder)` reads the project's own database: its status, its minutes (every session's wall
  time), its model calls, the retried ones (attempt 2 and later), reply tokens, spec points passing, parked and
  in all, README examples that failed, and the `slowest` steps (a step is its call's first word, so no file is
  named). `problems` lists, at most `REFLECTION_PROBLEMS`: what nothing made pass, failed README examples,
  failed, unusable and cut replies, and the warnings the runs said.
- **EXR-6** The exercise gives way as every job does (**JOB-8** to **JOB-10**): paused, it stops its build as
  Ctrl-C stops it (it saves itself), and goes on from where it was. A run an exercise started (its build) is
  not a job (**JOB-1**): its tracker goes in the exercise's job as `build` (**PRJ-14**), which only the
  exercise reads, so it is never among what runs, and the conversation's status shows the exercise when
  nothing else runs (looked at every `STATUS_LOOK_SECONDS` seconds, so it moves on by itself). The exercise's
  tracker shows its build's: its step after the exercise's, what the model writes now, the spec points passing
  and the latest line, and a practice project's steps only. As a conversation starts (`adopt_exercise`), it
  keeps up the message of an exercise whose job runs and whose tracker does not say it `reports` by itself
  (`Attached` says so), and an exercise whose job ended without being stopped (`Jobs.lost`: killed, the
  computer restarted) goes on as `resume #N`; either way its message is taken out and sent anew at the bottom.
  While it listens, it adopts again an exercise that went on by itself.
- **EXR-7** Ctrl-C or `stop #N` stops an exercise's build, keeps everything, and ends `stopped` with how to go
  on (`resume #N`, or `tinker exercise LANGUAGE`, goes on from there). `tinker exercise` takes a language or
  `status`. `exercise status` shows each exercise: its job's state (running, paused or stopped), its focus,
  each recent project's measures, and what it does now. In the conversation, `exercise LANGUAGE` (or
  `resume #N` of an exercise's job) starts it as a tinker of its own that outlives the conversation, its
  progress in its one message: every exercise has the same one (its ids kept in the home's `activity`),
  started, stopped (`stop #N` shows only there) or resumed, and the person's `exercise LANGUAGE` message is
  taken out; an exercise paused for another hands it its message, and gets it back when it goes on (the
  conversation adopts it again). One that ends before it keeps a tracker shows the last lines it printed.

## session (SES)

What every phase works through.

- **SES-1** `Session` is what every phase works through: the model, the project, its workspace and toolchain,
  the knowledge, the web plugin, the run's options (`web`, `confirm`) and its tracker. `Worker` and
  `Phase` take it, and nothing else, from the run, and reach the model, the project and its files
  only through it.
- **SES-2** `Session.setup` runs setup only when the setup commands or config files changed (or when forced),
  and it prepares `.env`. `run_all_tests` runs the suite once per state of the project's files. `note` keeps
  the last 30 problems. `ask_until_usable` gives the value, and ends with `SystemExit` when no reply was usable. `format_code` drops
  a formatter that fails.
- **SES-3** `knowledge_parts(*phases)` gives lessons and learned facts as optional parts, plus the person's
  commands for the toolchain. It records each item it shows in the cycle's `used`, with the prompt phases it
  was shown in. A prompt that writes code or tests is also told the checks they must pass: the toolchain's
  lint command, when it has one. `failed_phases(phase, stopped)` names the prompt phases whose knowledge a
  failure in that phase disproves: toolchain and config for setup, the final review or a tooling problem
  (`stopped` of kind `tools`); code and config when the project did not build (kind `build`); code, tests,
  design and spec for verify; none otherwise.
- **SES-4** `Session.save` keeps the project and gives the tracker the passing count.
- **SES-5** `Session.knowledge` is for the project's language. Before the spec names it, it is for the
  language the request names (`Knowledge.named_in`: the first it names of the languages Tinker knows), and
  Python only when it names none.

## planning (PLN)

Recall, the spec, the design and the contracts where files meet, and their review.

- **PLN-1** `Recaller`. With nothing learned and no web, it makes no call and uses no topic. Without the web,
  it uses the topics the request names or is close to. It keeps the chosen topics' facts in the project: tools
  and machine facts first, then a language's rules, then the other facts. The project's learned language is
  always used.
- **PLN-2** With the web, recall makes one call that names the learned topics to use and up to
  `learn_missing` new topics to learn (one per slug). Those are learned first, and they come first in the
  cycle's topics. When the call fails, the cycle goes on without learned topics.
- **PLN-3** A topic is used when the request names it, by its name or by its language without a version, or is
  close to it in meaning (`TOPIC_RELEVANT_SIMILARITY`, when embeddings are there). What the project does not
  need stays out of its prompts (`inapplicable`), proven or not: a rule for some code (its `when`) until the
  spec reply names that code among the listed `situations`, and anything that names a tool `unavailable_tools`
  gives.
- **PLN-4** `asked(cycle)` is the request, followed by each amendment the person made.
- **PLN-5** `Working.mistakes(text)` checks each chain of plain arithmetic (`=`-joined; `× ÷ − x` allowed). It
  reports each part whose value is not the chain's last value, within that value's rounding. A chain that goes
  on (`…`) may be truncated.
- **PLN-6** `SpecWriter.traced(source, request)` is true for `"setting"`, or when at least 60% of the source's
  words of 3 or more letters are in the request. Brackets in the source are ignored.
- **PLN-7** `SpecWriter.runs_program(text, entry, run)` is true when a criterion calls `main(` or the entry
  module, names the run command, reads as a command line that prints, or speaks of running, input, output or
  the exit code.
- **PLN-8** `SpecWriter.read_spec` drops a criterion not traced to the request, and needs at least one. It
  sends back wrong working (at most twice). It needs each test in a test file of its own, an entry that is one
  of the files, and (for the project itself) a criterion that runs the entry program and a toolchain without
  problems. A test file the plan does not list is taken as the one it lists with the same stem, or as the only
  one it lists. It defaults the language to Python. It leaves out a planned settings file (`.env.example`)
  when no criterion is about a setting. The spec's prompt shows what was learned for the spec, the design, the
  config files (which it plans) and the toolchain.
- **PLN-9** `SpecWriter.take` adds the criteria, puts the files into the design in build order (a new project
  gets a `README.md`), sets the toolchain and the entry, keeps the cycle's non-goals, constraints and data, and
  marks the cycle specified. When a user starts a file but the toolchain has no run command, the toolchain is
  asked for again. A cycle already specified is not planned again. A plan the person will confirm is a quick
  draft (think `low`).
- **PLN-10** `SpecWriter.amend` replaces the cycle's criteria with the amended spec, numbered from where they
  began, records the amendment and what changed (`+ ~ -`), and leaves the spec as it was when the model gives
  nothing usable. The cycle then plans its design and review again.
- **PLN-11** `Designer.order` puts config files first, then templates and code, each after what it uses, and
  tests and docs last. `read_toolchain_data` needs a run command when a user starts a file, and rejects a
  toolchain that has problems. `give_templates_a_renderer` gives an unused template to the code file whose role
  names it, or else to the entry. `drop_adapter_criteria` removes the criteria about an adapter.
- **PLN-12** `ContractWriter.order` puts each code file after the code it uses. `needed` is true for a file
  another code file or template uses, or one that renders a template. A file nothing uses, with no criterion,
  that is not the entry, is a marker. `write` sets the contract and adds the files it says it uses.
- **PLN-13** `InterfaceReviewer` checks only the files that use two or more contracted code files. It applies
  the corrections, checks the changed files again, and keeps its findings in the cycle's `review`.
- **PLN-14** `after_what_they_use(starts, uses, known)` orders the paths so that each comes after the paths it
  uses. It goes depth first from the starts, in their order. A cycle is broken where it closes. A path that is
  not known is left out.
- **PLN-15** `groups_fitting(items, room, size)` keeps the items in order, in groups that each fit `room` (each
  item takes `size + 1`). An item larger than the room is a group of its own.

## building (BLD)

Setup, the tests, the code, and fixing until every test passes.

- **BLD-1** `TestWriter` writes the missing tests, `TESTS_PER_CALL` per call. A test file that already holds
  tests is never rewritten: new tests go in a new file next to it (`test_x_2.py`), and their criteria follow
  them. A rewrite that loses a test is reverted. A corrected criterion updates the spec. A test that could not
  be written counts against its criterion.
- **BLD-2** `TestTool.ran_empty` is true when a test command, run before any test exists, exits 0, or says it
  found no test, or prints nothing that looks like an error. It is false for exit codes 126 and 127 and for a
  timeout. `works()` is true when setup, the test command and the linter run. A linter that is not installed
  is a tooling problem, and so is a one-test command that answers a made-up test name the same way as a real
  one.
- **BLD-3** `ToolchainRepair` replays a fix learned for exactly this failure, with no model call. Otherwise one
  model call may change only config files and the commands, and a fix that only made new files and commands is
  kept to replay.
- **BLD-4** `CodeWriter` writes the missing config files first, in one call. Then it writes one call per code
  or template file, in design order. A marker file is written empty, with no call. A new file goes where the
  design says. A file more than twice `MAX_FILE_LINES` long is asked for again, once. `wiring_problems` names
  each used file the code never mentions.
- **BLD-5** `Verifier.make_it_build` runs setup and the toolchain's check, and while the project does not build
  it asks `BuildFix` for a fix, at first without thinking, up to `BROKEN_BUILD_FIXES` times. `BuildFix` may
  change only the existing files it showed, and never a test.
- **BLD-6** `Verifier.run` gives True when every criterion passes. A failing criterion gets fixes until they
  stop making progress, then a review against the plan (`ladder`); a test file that does not load, or fixes
  that broke other tests twice, go straight to the review. A criterion that still fails is parked, and its files
  go back to how they were. A criterion that fails the same way as one already parked is parked at once.
- **BLD-7** A run stopped in the middle of a criterion leaves a restore point in the project's database. The
  next run puts the files back from it. `failure_key` ignores the test's own name. `roll_back` also drops the
  files created since.
- **BLD-8** `FixAttempt`. A fix changes only existing files that are shown, and never a test. A review may
  change the test and add new files next to it. A reply of `TEST WRONG` blames the test and changes nothing. A
  change that breaks a passing test is reverted, and that test is kept.
- **BLD-9** `BugReproducer` needs a new test file, given as `<file>::<name>`, and a test that fails now. It
  writes the test for the report and each amendment (`asked`). A report no test can show is dropped with its
  test, and the cycle closes as not reproduced.

## finishing (FIN)

Tidy, the README, the final review and the lessons.

- **FIN-1** `Tidy.findings(output, path)` counts the problems located in a file. `said_about` keeps the
  output's blocks that name the file. `reported` lists the files with findings, the most first.
- **FIN-2** `Tidy` gives each file the linter reports one call, with the file as numbered lines. A change
  stays only when the linter finds less in that file, every passing test still passes, and every test is still
  there.
- **FIN-3** `ReadmeWriter.examples(text)` gives `(command, expected lines)` for each `$ command` in a console
  block of the Usage section.
- **FIN-4** `ReadmeWriter.compare(expected, output)` ignores whitespace and skips lines with `...`. A
  `<placeholder>` matches any value, and the value is kept.
- **FIN-5** An expected line matches only a whole output line: `80.33` does not match `80.33000000000004`, and
  `7` does not match `17`.
- **FIN-6** `ReadmeWriter.read` needs features and at least one usage example. It keeps at most
  `MAX_USAGE_EXAMPLES` examples. A run command is served when the reply says so, or when an example talks to
  localhost.
- **FIN-7** `ReadmeWriter.text` writes: the title, the overview, the features, getting started (the `use`
  commands, how to run it), usage as console sessions, the settings (from `.env.example`), and development
  (setup, activate, tests, format, lint).
- **FIN-8** Every README example runs as a person runs it: after the `use` commands, with a server started
  first when the app serves. A failing example gets up to `EXAMPLE_FIX_ROUNDS` fixes, to the code or to the
  README. A fix stays only while every passing test still passes. What still fails is kept in the cycle's
  `readme_failures`.
- **FIN-9** `FinalReview` passes only when: setup runs afresh, the whole suite passes twice, the suite ran
  tests, and every criterion's test is there and passes. Otherwise it says what is wrong.
- **FIN-10** `LessonWriter` proves the knowledge the cycle used, and counts a finished project and keeps its
  toolchain once. With no problems noted, it makes no call. Otherwise it keeps at most 3 new lessons
  (**KNW-4**), giving the project's files and folder as the names a lesson must not have, and clears the noted
  problems.

## cli (CLI)

The main system: the command line, one run, and the cycle of phases.

- **CLI-1** `Cli.parse`. Every command is a word (`tinker task …`). An unknown command is an error. `confirm`
  runs as `resume` (of the plan that waits, **JOB-5**). A project is named only as `@ID` or `@folder`, before
  the command's words. `--workdir` defaults to `.`, except for `task`. A task's words are joined, and a last
  word that is an existing `.md`/`.txt` file is its spec. `rename` needs a valid folder name. A learn's
  words are joined into one topic, unless `taught_command(words)` reads them as a command of the person's: one
  word, then `=` and the definition (`gs = git status`, no quotes needed), or two words whose second is quoted
  (it has a space) or `none`. An option a command does not take is an error.
- **CLI-2** `tinker help` lists every command by group, the options, and examples. With
  `TINKER_FORMAT=markup`, it lists what a chat can send, each command with its `/`. `tinker help COMMAND` shows that command's arguments,
  and an unknown command is an error.
- **CLI-3** `Commands`. `model NAME` of a measured model uses it for every build; of one that does not embed
  and was never measured, it is a job that measures it (`Calibrator`) and then uses it, so a failed
  measurement keeps the model in use; of an embedding model, it checks the model gives a vector and makes it
  the embedding model. `model calibrate NAME` measures a model that does not embed again, and changes no model
  in use. `model recipe` prints the recipe, and `model recipe all` measures the models it lacks first
  (**MOD-31**). A NAME Ollama does not have is refused, with `tinker model` to list them. `model` and
  `model calibrate` with no NAME, on a terminal, print the recipe (**MOD-30**) and open a select box
  (**MSG-8**) on the rows (**MOD-29**), and the one chosen is taken as the NAME; a cancel changes nothing.
  `model` offers every model, the measured ones plain, the rest dim, starting at the one in use;
  `model calibrate` only the general ones, all plain, starting at the one in use, and picks before its job
  starts. Not on a terminal, they print the recipe and the rows; a chat with buttons asks with them
  (**CNV-13**). `runs` lists
  the runs, newest first. `status [@PROJECT]` shows the project's criteria and its open phase (in a chat,
  `status` alone shows what runs, **CNV-11**). `rename` renames the folder of a project whose plan waits, and
  its pointer. `config` shows the settings, and `config KEY VALUE` sets one.
- **CLI-4** `Doctor` checks Ollama, the model, the embedding model, the web and the command. It exits 1 only
  when a required check fails.
- **CLI-5** `tinker memory` browses what Tinker has learned (**GRF-6** to **GRF-12**); `--graph` draws it
  (**GRF-5**).
- **CLI-6** `tinker log [@PROJECT]` shows what the project's last runs said. `--calls` shows its model calls
  and details instead.
- **CLI-7** `tinker learn NAME = DEFINITION`, `tinker learn NAME "DEFINITION"` (or an alias in the person's
  shell) keeps the command, with no model. `tinker learn WHAT` needs a measured model, checked before anything
  is loaded, and an embedding model (PREFLIGHT, **LRN-3**). Learning nothing ends with an error. However it
  ends, it prints the time it took (`Time taken: 8m 34s`). It keeps a `Tracker` as a run does: its messages,
  what the model is doing (in plain words), its steps and its end (learned, with the summary, or why it
  stopped), so a chat that started it shows them; its last state stays in its job. `tinker research TOPIC` is
  the same, with its own steps: it researches the topic (**KNW-18**) and ends with its answer (**KNW-23**) as
  the ending's `text`.
- **CLI-8** `ProjectFolder.make(task)` makes a new folder in the projects folder. Its name comes from the model
  (when it is valid) or from the task's words, and it gets `-2`, `-3` … when the name is taken.
- **CLI-9** A run works on one cycle. For `add` or `fix`, that is a new feature or fix cycle, recorded in the
  run's `features`. Otherwise it is the unfinished cycle, or else the project's own (`base`). A new cycle waits
  for confirmation only when the run was given `--confirm`, and keeps that for the runs that go on with it.
- **CLI-10** `tinker task` builds straight through ([The cycle](#the-cycle)). Given `--confirm`, it prints the
  plan to confirm and ends `awaiting` (**CLI-9**, **PRJ-13**). A run that stops (`blocked`) says why and how
  to go on.
- **CLI-11** `CycleRunner.run` runs the phases from the saved one, in order, saving after each, and formats
  after verify. A run with `--web` that is past recall recalls first. It gives `blocked` when a phase stops,
  and then disproves the knowledge used in that step. A phase that stops for a reason gives it as its result,
  `Stopped(kind, reason)`: the tools do not work (`tools`), the project does not build (`build`), the bug was
  not reproduced (`unreproduced`) or the final review failed (`review`). The runner keeps it, and the run's
  end says why from it. A phase that stops with parked criteria gives False. It gives `awaiting` after the
  phase its kind confirms after (`CONFIRM_AFTER`) when the cycle waits for confirmation (**CLI-9**), and
  `done` otherwise. Every phase has a phase class, and each phase done says what came of it (`outcome`: the
  points to test, the files, the tests and files written, the spec points passing).
- **CLI-12** `main()` ends a run cleanly: Ctrl-C ends with `Interrupted.`, and Ollama and file errors end with
  their message, never a traceback. A closed terminal and `SIGTERM` end it as Ctrl-C does. However it ends, it
  unloads no model: a model stays loaded until its `keep_alive` idle time is up, so the next run does not load it
  again. A chat, however it ends, unloads the model in use and the embedding model, so none of its jobs' models
  stays in memory after it.
- **CLI-13** Built end to end: `tinker task` with a model that answers like a real one builds a project in its
  folder. Each spec point gets a test, the code passes them (a wrong first version is fixed by the verify
  loop), the README's examples run, the final review passes, lessons are kept, SPEC.md is written, the run is
  pointed to from the home and the exit code is 0.
