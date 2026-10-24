# Tinker: Specification

Tinker builds what a request asks for with a local model run by Ollama. It plans the work as a small
spec, writes one unit test for each spec point, writes just enough code to pass them, and checks the
result the way a person uses it. It learns from every run. This document says what Tinker is and does.
The tests in `tests/` enforce it.

## How to read this

- Each numbered point (**SET-1**, **WEB-3** ...) states one behaviour. At least one test enforces it,
  marked `@pytest.mark.spec("SET-1")`. `tests/test_spec_coverage.py` checks that every point here has
  a test and that every test names a point that exists.
- Run the tests from the repository root: `python -m pytest` (after `pip install -r requirements.txt`).

## The idea

1. Build only what the request clearly asks for. Anything else waits for `tinker add` or `tinker fix`.
   Nothing is made or changed beyond what the spec of the project needs: every file serves a spec point.
2. The plan is a spec of points. Each point has exactly one unit test, written before any code.
3. The code is just enough to pass those tests.
4. The project is done when every spec point's test passes, set up afresh, the way a person runs it.
5. In the conversation, the person confirms a plan before anything is built. A run started from the
   command line builds autonomously, unless it is asked to wait (`--confirm`).
6. Tinker keeps what it knows in its own database, and each project's state in the project's own
   database. People read them through Tinker's commands. They never edit them by hand.

## Principles

- **Tinker's home is Tinker's alone.** Its settings change only through its commands. Its memory (what
  it measured, learned and was taught) changes only through its own steps: calibration, learning,
  a command the person gives, and the lessons step at the end of a cycle. A project's files never
  change Tinker's home.
- **A project's state is the project's alone.** Tinker keeps it in the project's `.tinker` folder. The
  home keeps only a pointer to each project (its run ID, its folder and its task) and one line about
  each run (its task, language, status and dates), so what a run taught can be traced after its folder
  is gone.
- **Nothing of Tinker's is put among a project's own files**, except `SPEC.md`, which is the plan,
  written for people.
- **A project is named only explicitly**, as `@ID` (its full run ID) or `@folder` (its folder's name,
  exactly). Tinker never guesses a project from the words of a request.
- **Knowledge must be proven.** What Tinker learns starts as a candidate. A cycle that succeeds while
  using it proves it. A failure in the step it was meant to help retires it, even when it was proven
  before: it is kept apart, with why, for people to see, and never shown to a model again. Each item
  keeps its history: the run that taught it, and the runs it helped or failed.
- **A lesson is general.** It may hold for one language or for any language. It never names a
  project, its files, its names or its data.
- **Every part reports through one tracker.** A run publishes what it is doing as structured state.
  The terminal and the chats show that state. They never read meaning back out of printed text.

## Architecture

`tinker.py` is the main system: the command line (`Cli`, `Commands`, `Doctor`), one run (`ProjectRun`,
`ProjectFolder`) and the cycle of phases (`CycleRunner`). It uses the `tinkerkit` modules. Each has one
responsibility, and each imports only the modules listed above it:

| Module | Responsibility |
|---|---|
| `settings` | every tunable number and fixed name; the Tinker home (`Home`), its database (`Store`) and `Config`; small helpers |
| `messages` | everything Tinker tells the person, in one voice (`say`, `Messages`, `Message`): each message's kind gives its icon, sections nest it, followers (a tracker) get it as data, and details go to the log only |
| `jobs` | every long command as a job, listed, stopped and resumed (`Jobs`), and its journal of requests and answers (`Journal`, `journaled`) |
| `ledger` | what is said in the chats, so a chat is only a view of it: each message with when it goes, each thing shown, numbered, for `history` (`Ledger`) |
| `commands` | the one table of Tinker's commands, and project references (`@ID`, `@folder`) |
| `prompts` | prompt parts (`Part`), replies (`Reply`), every system prompt (`Prompts`) |
| `workspace` | the project's files and the commands run in it (`Workspace`, `Toolchain`, `TestRef`, `Output`, `FileView`, `TaughtCommands`) |
| `model` | the local model: Ollama's API, one request with its budgets and retries (`Model`), measuring a model (`Calibrator`) |
| `project` | the plan and its record (`Project`, `SpecDocument`, `Runs`, `RunChecks`, `Brief`, `Outcome`), and the run's `Tracker` |
| `fetcher` | the Internet plugin's page fetcher: pages read straight from their sites, in parallel, with the standard library only (`Fetcher`, `Fetched`) |
| `web` | the Internet plugin, the only part that goes on the web (`Internet`, `OllamaWeb`, `DuckDuckGoWeb`, `PageText`) |
| `passages` | fetched pages as passages: main content, under headings, repeats dropped (`Cleaner`, `MainBlocks`, `Links`, `Page`, `Passage`) |
| `conversation` | the terminal and the chats as one conversation (`Desk`, `Channel`, `Markup`, `Talk`, `Shell`, `Research`, `Progress`, `Reporter`) |
| `channels` | the chat plugin: Telegram, and `tinker channel` |
| `knowledge` | lessons and proven knowledge (`Knowledge`), and what learning shares (`Learner`, `Researcher`, `Library`, `Retriever`, `Embeddings`) |
| `slots` | what learning a language or a tool must find out, as data (`Slot`, `PROFILES`), and each slot's passages (`Selector`) |
| `docsite` | a topic's documentation site for its version: its map, each slot's pages, kept per topic and version (`DocSite`, `DocCache`) |
| `learning` | learning a language or a tool by its slots (`SlotLearner`, `Throttle`, `RequestLog`, `LearnStopped`) |
| `graph` | what Tinker knows as one graph of items and links (`Graph`), and the page that shows it (`GraphPage`) |
| `reading` | what the person gives to read: stored whole on the reading list (`Readings`), then learned from chapter by chapter (`Reader`, `Epub`, `Book`) |
| `teaching` | asking what Tinker keeps (`Asker`), and teaching it, checked before anything changes (`Teacher`, `Memory`, `Verdict`) |
| `exercising` | practising a language on its own, giving way to the person (`Exercise`, `Exercises`) |
| `session` | what every phase works through (`Session`), and the phase contract (`Worker`, `Phase`) |
| `planning` | recall, the spec, the contracts where files meet, and their review |
| `building` | setup, the tests, the code, and fixing until every test passes |
| `finishing` | tidy, the README, the final review and the lessons |

- **ARCH-1** Each `tinkerkit` module imports only the modules listed above it in the table. The one
  exception is `conversation`, which imports the `channels` plugin lazily, inside a function.
- **ARCH-2** The channels plugin is optional. Without `tinkerkit/channels.py`, the conversation has no
  chat channels, and everything else works.
- **ARCH-3** Every model call goes through `Model` and `Ollama`. Every web call goes through
  `Internet` (pages through its `page_fetcher`, the `fetcher` module). Every project command goes through `Workspace.run` or `Workspace.start`.
- **ARCH-4** Only `settings.Home` knows where the home is. No other module names a path inside it.
  Every phase reaches the model, the project and its files through the `Session` only.

## State

The Tinker home is the `.tinker` folder next to the real `tinker.py` (symlinks resolved). It holds one
SQLite database, `tinker.db` (mode 600), the lock files of the chat channels it listens to, and
`memory.html`, the page `tinker memory --graph` writes, and `reading/`, the files sent in a chat to read. The
database holds:

| Collection or log | What |
|---|---|
| `config` | the settings that differ from `DEFAULT_CONFIG` |
| `models` | what calibration measured for each model |
| `languages` | per language: the toolchain that worked, toolchain fixes, lessons, proven facts |
| `memory` | lessons for any language |
| `learned` | what was learned from the web about each topic, with sources, and what each reading teaches |
| `readings`, `reading numbers` | the reading list: each source's whole text by chapter, each chapter's state, its survey; the last number given |
| `embeddings` | the embedding of each learned text, per embedding model |
| `commands` | the person's own commands (aliases, programs) |
| `projects` | a pointer to each project: its run ID, folder and task |
| `runs` | one line about each run: its task, language, status and dates (the last `RUNS_KEPT`) |
| `channels`, `channel_secrets` | the chat channels and their tokens |
| `listening`, `running` | the conversation's live state: its status messages, and each run's tracker |
| `exercise` | per language: its exercise's practice projects, what each measured, and the focus of the next |
| `activity` | the person's requests being answered (`answering`), and the exercise that runs now |
| `chat views` | each message in a chat (Tinker's and the person's): when it goes, and the thing it shows |
| `chat history`, `chat history numbers` | each thing shown in a chat, as it was shown last, with what the person asked for it; the last number given |
| `jobs`, `job numbers` | every job: its number, command, title, arguments, state, process and dates; the last number given |
| log `learn` | every model call that `tinker learn` made |
| log `teach` | every model call that `tinker ask` and `tinker teach` made |
| log `read` | every model call that `tinker read` made |
| log `exercise` | every model call that `tinker exercise` made itself (its builds keep theirs) |
| log `journal #N` | job #N's requests (model calls, web searches, page fetches) with their answers, until it finishes |

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

A cycle is the project itself (`base`), a feature (`tinker add`) or a bug fix (`tinker fix`). Its phases
run in order, each saved as it ends, so a run resumes from the phase it reached:

`recall → spec → design → review → setup → tests → code → verify → tidy → docs → final_review → lessons → done`

A bug fix runs `recall → reproduce → verify → final_review → lessons → done`.

A cycle started from the conversation, or with `--confirm`, waits for the person once its plan is
made: the project or a feature after `review`, a bug fix after `reproduce`. `confirm` builds it.
`amend CHANGE` changes it and plans it again, and then it waits again. A cycle started from the
command line without `--confirm` builds straight through. `--web` lets any run, at any
phase, learn from the web what the rest of the cycle needs.

---

## settings

- **SET-1** `Home.root` is `.tinker` next to the package's real location. `Home.store()` is the home
  database, `tinker.db`, in it.
- **SET-2** `DEFAULT_CONFIG` is exactly: model `None`, ollama_url `http://localhost:11434`, keep_alive
  `0`, max_growth `1.15`, build_think `"medium"`, test_timeout `60`, web `"ollama"`, web_fallback `""`,
  web_timeout `30`, learn_missing `2`, learn_rate `8`, learn_backoff `20`, learn_cutoff `0.0`, learn_pages
  `8`, learn_min_pages `2`, learn_searches `3`, learn_think `"low"`, docs_pages `30`, tool_docs_pages `5`,
  docs_latest_days `30`, fetch_workers `6`, fetch_timeout `15`, fetch_max_bytes `2000000`,
  fetch_domain_delay `0.1`, fetch_fallbacks `2`, embed_model `""`, projects `"~/Projects"`.
- **SET-3** `PHASES`, `FIX_PHASES` and `PLANNING_PHASES` are the phase lists given in
  [The cycle](#the-cycle). `CONFIRM_AFTER` is `review` for the project and a feature, and `reproduce`
  for a bug fix.
- **SET-4** `Config` is `DEFAULT_CONFIG` overridden by the home's `config`. It is read from the
  database each time, so a change another Tinker made is seen at once. `Config.save(**changes)` keeps
  the changes.
- **SET-5** `Config.check_model(run)` rejects with `SystemExit`: a keep_alive that is neither whole
  seconds nor a duration (`10m`, `1h30m`); a max_growth that is not a number above 1 (booleans
  rejected); a build_think or learn_think other than `"high"`, `"medium"`, `"low"`, `true`, `false` or
  `null`. For a
  run, it also rejects a test_timeout that is not a whole number of at least 1.
- **SET-6** `Config.keep_alive(value)` gives whole seconds as an `int` and a duration as it is.
  `Config.ollama_url()` has no trailing slash.
- **SET-7** `Store(path)` is one SQLite database of documents (JSON values) by collection and key.
  `get` gives the default for a missing document. `put`, `delete` (one document, or a whole
  collection) and `items` work as named. `update(collection, key, change, default)` reads, changes
  and writes a document in one transaction, so two processes never lose each other's change. A
  write is all or nothing: a crash leaves the old value or the new one.
- **SET-8** `Store.append(log, entry)` adds an entry with its time to a log. `Store.entries(log,
  after)` gives the entries after an ID, in order, with their IDs. A database file is made with
  mode 600.
- **SET-9** `Home.ensure()` makes the home folder and its database on first use. A home that cannot
  be written ends with `SystemExit`.
- **SET-10** `Home.reset(yes)` deletes the home folder. It refuses a folder not named `.tinker`. It
  does nothing when the folder is missing. Without `yes`, and not in a terminal, it deletes nothing
  and exits.
- **SET-11** `Home.parts()` names exactly: languages, memory, learned, docs, models, runs, commands, history,
  exercise.
  `Home.clear(names, yes)` deletes only the named parts from the home database. It rejects an unknown
  part by listing the parts. When those parts are empty, it says there is nothing to clear. Without
  `yes`, and not in a terminal, it deletes nothing.
- **SET-12** Helpers. `first_line` gives the first line of the text with its ends stripped, cut to the limit
  with `...`. `words` gives the lowercase alphanumeric words. `fingerprint` is the SHA-1 of the text.
  `similarity` is a case-insensitive ratio from 0 to 1.
- **SET-13** `split_text(text, size)` gives chunks of at most `max(size, 200)` chars. It splits
  between paragraphs, cuts a longer paragraph, and loses no text.
- **SET-14** `after_what_they_use(starts, uses, known)` orders the paths so that each comes after
  the paths it uses. It goes depth first from the starts, in their order. A cycle is broken where
  it closes. A path that is not known is left out.
- **SET-15** `groups_fitting(items, room, size)` keeps the items in order, in groups that each fit
  `room` (each item takes `size + 1`). An item larger than the room is a group of its own.
- **SET-18** `Home.keep_awake()` keeps the computer awake with `caffeinate` (macOS) or
  `systemd-inhibit` (Linux), tied to Tinker's process. It does nothing when neither tool is there,
  and it never raises.
- **SET-19** `Config.set(key, text)` is `tinker config KEY VALUE`. It sets only the keys that have no
  command of their own (`ollama_url`, `keep_alive`, `max_growth`, `build_think`, `test_timeout`,
  `web_fallback`, `web_timeout`, `learn_missing`, the `learn_*`, `docs_*` and `fetch_*` settings, `projects`), reading
  the value by the key's type. A
  value that fails the checks is not kept, and the command ends with why.

## messages

Everything Tinker tells the person goes through one voice, `say` (a `Messages`). No other module prints.

- **MSG-1** Each message has a kind, and its kind gives its icon: done ✅, warn ⚠️, fail ❌, retry 🔁,
  skip ➖, stop 🛑, tip 💡, item •. A title, a step and an info take their own icon; plain text has
  none. A line is its icon, a space and its words; after an emoji drawn with a variation selector
  (⚠️), two spaces, so the words of every line stay in one column.
- **MSG-2** A section nests what is said inside it three spaces deeper. A part is a title with a
  section under it. A title at the top has a blank line before it. A summary is a part whose lines are
  `label: value`, each with its icon. `took` says something done with how long it took.
- **MSG-3** Every message is given, as data (`Message`: kind, text, depth, icon), to each follower.
  A follower that fails never breaks what is said. A run's and a learn's `Tracker` follow: a message
  becomes the latest and goes to the project's `output` log; a live one becomes what goes on now.
- **MSG-4** `live` redraws one status line in place on a terminal only. Piped (a chat following the
  command), it writes nothing; its followers are still told. The next message clears it first.
- **MSG-5** `detail` goes to the log given to `keep_log` (a run's `events`, tinker learn's `learn`),
  and is never printed. With no log, it is dropped.
- **MSG-6** `human_duration`: 42.5 gives `42s`, 1762.08 gives `29m 22s`, 4000 gives `1h 6m 40s`.
- **MSG-7** No module of Tinker but `messages` calls `print`.

## ledger

- **LED-1** `Ledger` keeps each message by channel, chat and id: `kept` goes in `VIEW_KEEP`, `touched` moves
  that on (but a glance's, or the status'), `glance` in `GLANCE_KEEP` (or the seconds given), `lasting`
  never (`is_lasting` tells it). `due` gives the messages whose time is up, `passing` all but the status,
  `glances` a chat's glances; `gone` forgets one.
- **LED-2** `shown(channel, chat, thing, ids, text, asked)` keeps a thing as it was shown last; with no thing,
  a new one is numbered, keeping what was asked. `thing_of(ids)` gives the thing messages show. `page(N)`
  lists things newest first, `PAGE` to a page, with how many pages there are.

## jobs

Every long command is a job: it can be listed, stopped and resumed the same way, and a resumed job does not do
again what it did. One job runs at a time, whatever kind it is: the job asked for last is the one that runs, and
the one it paused goes on by itself once Tinker is idle. A chat or a command line the person asks for pauses it
too: they are answered at once, so there is no emergency command.

- **JOB-1** `is_job(argv)`: `task`, `restart`, `resume`, `confirm`, `amend`, `add`, `fix`, `learn`, `ask`,
  `teach`, `read`, `research` and `exercise` are jobs; help, `exercise status`, `read list`, `read show #N`,
  `learn NAME "DEFINITION"` and a build an exercise runs (`EXERCISE_ENV`) are not. `Jobs.running(argv)` runs a job as the next number (`#1`, `#2` ...),
  kept in the home's `jobs` with its command, title, arguments, the day it started, its state `running` and its
  process. Its process takes `STOP` (SIGUSR2) as Ctrl-C.
- **JOB-2** A job ends `stopped` when it is interrupted (Ctrl-C, `STOP`) or its tracker ends with the status
  `stopped` (`Tracker.end` tells `Jobs.ended`, which adds `("Go on", "tinker resume #N")` to the ending), and
  `paused` when another job or the person paused it ("Paused for job #N", "Paused for you": its ending says so,
  and its tracker's `doing`); otherwise it is
  `finished`, and its journal is deleted. A `running` or `paused` job whose process is gone (killed, the computer
  restarted) is read as `stopped`.
- **JOB-3** `journaled(request, ask)` outside a job asks. In a job, a request made before, word for word, is
  answered from the job's journal (log `journal #N`) in the order the answers were kept, and nothing is asked;
  any other request is asked, and its answer kept unless it failed (`keep`). `Ollama.chat` (but an unload),
  `Internet.search`, `Internet.fetch` and `Fetcher.fetch` go through it.
- **JOB-4** `today()` is the day the job that runs started (today outside a job): the prompts that say the date
  (learning's plan, research's frame, `research TOPIC`) say the same on the day a job is resumed.
- **JOB-5** `Jobs.arguments`: `resume #N` runs job #N's arguments, as that job (its number, its journal);
  `resume` alone runs the job stopped last, and with no job stopped it is the folder's run, as before;
  `resume @PROJECT` is unchanged. An unknown job, one that runs in another process, and a finished one are
  refused with why. A task's job is resumed as `resume @RUN` once its run is known (`Jobs.retarget`). A job that
  goes on (`resume #N`) starts its tracker with `resumed`, so its message comes to the bottom of the chat.
- **JOB-6** `tinker jobs [running|paused|stopped|finished] [PAGE]` lists the jobs newest first, `PAGE` (10) to
  a page: with no state, the running ones, then the paused ones, then the stopped ones. A page that has more
  after it names the command for the next. Any other words are an error.
- **JOB-7** `tinker stop #N` sends job #N `STOP`, as Ctrl-C (one that neither runs nor is paused is an error);
  `tinker stop` stops the job that runs, whatever it is. In the conversation, `stop #N` stops that job (the task
  started here, the exercise in its own message, or one elsewhere) and `stop` the job that runs, and
  `resume [#N]` starts `tinker resume #N` as a task (an exercise as the exercise). A paused job that is stopped
  no longer goes on by itself.
- **JOB-8** One job runs at a time, whatever kind it is. A job that starts (`Jobs.begin`) pauses every job that
  runs: each is marked `paused_by` it and told to stop (`STOP`), and it waits, at most `PAUSE_WAIT` seconds, while
  they save themselves. A paused job keeps its process: it waits there (`go_on_when_idle`). No rule refuses a job
  because another runs: not the conversation's task, nor a second exercise.
- **JOB-9** A paused job goes on by itself, at once, as `resume #N` in its own process, as soon as Tinker is idle
  (`idle`: no job runs, and no request of the person's is being answered: `answering`, in the home's `activity`,
  kept by `paused_for_you`; a request of a process that is gone does not count). There is no quiet time to wait
  out. Of the paused jobs, the newest goes on first (`next_to_go_on`), so one at a time. `resume #N` of a paused
  job makes it go on at once (`Jobs.wake`), in the process where it waits: a paused job never runs in a second
  process.
- **JOB-10** A request the model answers right away, a chat or `command WHAT` (in the conversation, or `tinker
  chat` and `tinker command`), pauses the job that runs while it is answered (`paused_for_you`: paused by "you").
  The request is marked as being answered before anything is paused, so the paused job does not go on the moment
  it has saved itself. The person is answered as soon as it has, and the job goes on as soon as the answer is
  written (a command line run once confirmed pauses it again while the model reads its output). A command that
  only shows something pauses nothing, and a job asked for pauses it as **JOB-8** says. There is no `emergency`
  command.
- **JOB-11** `tinker clear jobs [#N...] [--yes]` archives those jobs (with no `#N`, every stopped and finished
  one) once the person agrees: each is kept with the state `archived` (and `archived_from`, the state it had),
  listed by `tinker jobs archived` and not without a state, and its journal is deleted. An archived job is not
  resumed (**JOB-5**). An unknown job, a running or paused one, one archived already, and any word but `#N` are
  refused with why.

## commands

- **CMD-1** `COMMANDS` is the one table of Tinker's commands. For each it gives the synopsis, the
  group, what it does, its kind (`run`, `long`, `quick`, `deletes`, `own`), the words it takes in a
  chat (`none`, `one`, `any`), its arguments and its options. The command line builds its parser and
  its help from it, and the conversation its commands.
- **CMD-2** The runs are `task`, `restart`, `confirm`, `amend`, `add`, `fix` and `resume`. Every run
  but `restart` takes `--web`. The runs that start a cycle (`task`, `restart`, `add`, `fix`) take
  `--confirm`, and the others do not. The commands that delete are `clear`, `reset` and
  `restart`.
- **CMD-3** `ref(word)` reads a project reference: `@` followed by a run ID or a folder name. Any
  other word is not a reference. `refs(words)` takes a reference from the start of the words, and
  leaves the rest as they are.

## web

- **WEB-1** `Internet.backends()` is `{"ollama": OllamaWeb, "duckduckgo": DuckDuckGoWeb}`.
- **WEB-2** `Internet()` uses config `web`, then `web_fallback`. It skips empty and repeated names.
  A backend that is not ready is left out, and its reason goes in `skipped`. With no backend
  left, `available()` is False and `why()` gives the reasons.
- **WEB-3** `Internet.check()` rejects with `SystemExit`: an unknown `web` or `web_fallback`; a
  `web_timeout` that is not a number above 0; a `learn_missing` that is not a whole number of at
  least 1 (booleans rejected).
- **WEB-4** `Internet.required(option)` ends with `SystemExit` naming the option when no backend
  is usable. `Internet.state()` gives `ready: <backends>` (and any backends that are not usable),
  or why it is not usable.
- **WEB-5** `search` keeps only the results with an `http(s)` address, each as
  `{title, url, content}` strings. `fetch` gives `{title, url, content}` for the address asked
  only when its content has text. When no backend answers, either one gives `None`, and
  `why(key)` gives each backend's reason.
- **WEB-6** When a backend fails, the next backend is asked. A `WebError(retry=True)` is asked once
  more after `RETRY_PAUSE`. Any other exception from a backend counts as a failure; it is never
  raised.
- **WEB-7** After `GONE_AFTER` network failures in a row, a backend is not asked again. A success
  resets its count. Once no backend is left, the plugin is gone.
- **WEB-8** `OllamaWeb` is not ready without `OLLAMA_API_KEY`. It POSTs JSON with
  `Authorization: Bearer <key>` to `/web_search` and `/web_fetch`. HTTP 401, 403 and 429 are
  network failures. HTTP 429 and 5xx are worth asking again. A fetch's 5xx is not a network
  failure. An answer that is not JSON, or a search that has no result list, is a `WebError`.
- **WEB-9** `DuckDuckGoWeb` is always ready. A search reads the result links, unwraps the `uddg`
  redirect, drops ads (`y.js`), takes the snippets, and gives at most `count` results. A bot-check
  page is a `WebError` worth asking again.
- **WEB-10** `DuckDuckGoWeb.read_page` gives an HTML page as its main text (`PageText`). Plain
  text, JSON and XML stay as they are. Any other content type is a `WebError`.
- **WEB-11** `PageText` gives a page's title and main text. It uses `<main>` or `<article>` when
  that holds at least 400 chars, and the whole body otherwise. It leaves out scripts, styles,
  navigation, headers, footers, asides, forms, `hidden`, `aria-hidden` and navigation roles.
  Headings become `# ` lines, list items become `- ` lines, and `<pre>` keeps its layout. Broken
  HTML gives what was read before it broke.
- **WEB-12** A `WebError` carries the HTTP status it answered with (`code`), and
  `Internet.rate_limited(key)` says whether that key's last failure was HTTP 429.
  `Internet.page_fetcher(log)` is a `Fetcher` set up from the `fetch_*` settings. `Internet.check()`
  also rejects a `learn_*`, `docs_*` or `fetch_*` limit that is not a number in its range (`LEARN_LIMITS`).

## prompts

- **PRM-1** `Reply(raw).text` is the raw reply without `<think>…</think>`, stripped.
- **PRM-2** `Reply.json()` gives the JSON object in the reply, with prose around it allowed. It
  mends, only when needed: commas before a closing bracket, strings joined with `+`, an escaped
  single quote (`\'`), brackets left open at the end, and a list left open before the next key.
  No object is a `ValueError`. What cannot be mended raises the reply's own `JSONDecodeError`.
- **PRM-3** `Reply.files()` gives `(path, body)` for each `<<<FILE path>>> … <<<END>>>` block.
  The header may be `path=x`, `file: x`, `"x"`, or have no `>>>`. A placeholder path (`path`,
  `file` …) followed by the real path, on the header line or on the first line, takes that real
  path. A body that is only the format's placeholder is skipped. Stray format marks (`>>>`,
  `<<<…>>>`) at the ends of a body are dropped. One markdown fence around the whole body is
  dropped, except in a Markdown file.
- **PRM-4** `Reply.line_edits()` gives `(path, first, last, lines)` for each
  `<<<LINES path A-B>>>` block. The line-number prefixes (`12| `) are removed when every line has one.
- **PRM-5** `Reply.as_file(path)` turns a reply that has no FILE block but exactly one fenced block
  into that file. Any other reply stays as it is.
- **PRM-6** `Reply.files_ended(content)` is true when the last block has ended and nothing after it
  begins a block. `Reply.files_complete(content)`, which stops a stream early, also needs at least 300
  chars after it (the model is running on). `Reply.wrote(paths)` is also true once every wanted path
  has its block.
- **PRM-7** `Reply.json_complete(content)` is true once the content ends with `}` and the object from
  its first `{` parses (trailing commas allowed).
- **PRM-8** `cause()`, `test_wrong()`, `criterion()`, `corrected_criteria()` and `toolchain_line()`
  read their marker lines (`CAUSE:`, `TEST WRONG:`, `CRITERION:`, `CRITERION <test>:`,
  `TOOLCHAIN: {json}`). `named_paths()` gives each path named by a block, once, in order.
- **PRM-9** `Reply.spec_json()` keeps the rest of a spec, with `data` as text, when only its last
  part (`"data"`) is broken.
- **PRM-10** `Reply.strip_trailing_commas` removes a comma before `]` or `}` only outside strings.
- **PRM-11** `Part.assemble(parts, limit)` lays parts out by `place`: `"first"`, then unplaced, then
  `"last"`, each group in its given order. Required parts are always included. An empty optional
  part is left out. Optional parts are added while they fit. A non-atomic optional part is
  shortened to the room left (if at least `SHORTEST` chars). An atomic one is whole or left out.
  When the required parts fit, the result fits the limit.
- **PRM-12** When the required parts do not fit, the longest prose part is shortened first, never
  below `TRIM_FLOOR`. A `head` part keeps its start, a `tail` part its end, with a `[...]` mark.
  Exact parts are never shortened. When nothing more can be shortened, `PromptTooLarge` names the
  exact parts. The shortened titles are added to `notes`.
- **PRM-13** A part is `exact` only when it is required, and `atomic` only when it is optional.
  `relaxed()` is the same part, but not exact. A part renders as `## title\ntext`, and its size is
  the rendered length + 2.
- **PRM-14** The example JSON in `SPEC_SYSTEM`, `FEATURE_SPEC_SYSTEM`, `TOOLCHAIN_SYSTEM` and
  `README_SYSTEM` parses. Its toolchain is a usable `Toolchain`. Every `{placeholder}` template
  (`BRIEF_SYSTEM`) formats.
- **PRM-15** Each command in the example toolchain is a real command line of its own tools.
  Tinker's own words never appear in it.
- **PRM-16** `LESSONS_SYSTEM` asks for general lessons only: never a project's names, files or data.

## workspace

- **WS-1** `Workspace.run(command)` runs one command in a POSIX shell in the project folder. Input
  comes from `/dev/null`. stdout and stderr are merged, and the project folder's path is removed from
  the output. It gives `(exit code, output)`. A command that times out gives `(None, output)`, and its
  whole process group is killed. An unsafe command is never run: it gives `(None, REFUSED)`.
- **WS-2** These commands are unsafe: `sudo`; `rm -rf` of `/`, `~` or `..`; `mkfs`; `dd if=`; a
  download piped into a shell; a redirect into `/etc`, `/usr`, `/bin` or `/dev/sd*`. Ordinary
  build and test commands are not.
- **WS-3** `Workspace.resolve(path)` accepts a path relative to the project and inside it. It
  rejects with `ToolError`: an empty path, an absolute path, a path starting with `~`, a path that
  leaves the project, a path that is too long or has odd characters, and a write into `.tinker` or
  `.git`. The root is taken resolved, so a root reached through a symlink works the same.
- **WS-4** `write` makes the parent folders and ends a non-empty file with a newline. `read` of a
  missing file gives `""`. `read` and `exists` see only files inside the project: a path that
  leaves it reads as `""` and does not exist.
- **WS-5** `apply(reply, may_change)` writes each FILE block that `may_change(path, exists)` allows,
  and keeps a backup (`None` for a new file). It refuses with a note: a placeholder path, a path
  outside the project, a file shown only in part, and an empty body where empty is not allowed.
  `revert(change)` restores each backup and deletes the files the change created.
- **WS-6** A LINES block changes only a file shown as numbered lines, and only within the lines
  shown. Of two overlapping blocks, the earlier one in the reply is applied and the later one is
  dropped, with a note. A file shown in part takes no FILE block.
- **WS-7** `prepare_env()` makes `.env` from `.env.example` when there is none. Only the values that
  are obvious placeholders are blanked, and it says which settings to fill in. An existing `.env` is
  never changed. `is_placeholder` is true for an empty value, `<...>`, `your_...`, `..._here`,
  `changeme`, `replace-me`, `xxx`, `***`, `...` and `placeholder`. It is false for any other value,
  such as `none`, `null`, `PUT`, `todo` or `example_db`.
- **WS-8** `start(command)` starts a long-running command in the background, with its output in
  `.tinker/app-output.txt`. `stop(process)` ends it and everything it started. An unsafe command
  is not started.
- **WS-9** `TaughtCommands.teach(name, definition)` keeps an alias in the home's `commands`. A
  `source` in it becomes `.`. The definition is checked as POSIX shell. The name must be a shell
  function name. `"none"` forgets the command; forgetting one never taught ends with `SystemExit`. An
  empty definition ends with `SystemExit`.
- **WS-10** `TaughtCommands.all()` rejects entries that are not aliases or programs. `spelled_out`
  puts each alias's definition in its place, as whole words only. `prelude(command)` defines, as
  shell functions, the aliases the command uses. `Workspace.run` puts the prelude first, so a taught
  alias works in a project's commands.
- **WS-11** `Toolchain(data)` makes lists of `setup` and `use`. It leaves out the setup commands that
  write `.env` (kept in `dropped`) and those that change the machine's own tools (kept in `machine`).
  A lint of `"none"` means the language has no linter: `lint` is `""`, and `to_dict()` gives
  `"none"` back. A toolchain is true only with both `test_all` and `test_one`.
- **WS-12** `shell(command)` runs the command after `activate`, when there is one.
  `merged(changes)` gives a toolchain with the changes: an empty command keeps the old one, and a
  list, even an empty one, replaces the old list. `problems()` names a missing `test_all`, a
  missing `test_one`, a missing lint, and a `test_one` with neither `{name}` nor `{file}`.
- **WS-13** `run_setup(ws)` runs the setup commands in order. A command that fails stops setup with
  `$ <command>  failed (exit code N)` and its output. A failure caused by the network is set aside
  as waiting, and the rest go on. A command that makes a new project in a subfolder stops setup.
  Once `activate` works, later commands run in that environment.
- **WS-14** `run_test(ws, ref)` gives `"passes"` only when the command exits 0 and ran a test. A
  command that ran no test, failed, or did not finish within `test_timeout` gives `"fails"` with
  why. `run_all` notes a suite that did not finish.
- **WS-15** `ran_no_test(output)` is true when the output says no test ran, and no part of it
  says tests ran. `reached_no_test` is true when the test command failed before reaching any
  test: exit code 126 or 127, refused, or no test file or name in its output. A timeout does not
  count.
- **WS-16** `TestRef("f::n")` has file `f` and name `n`, and a bare path has no name.
  `command(template)` fills in `{file}`, `{name}` and `{id}`. `present(ws)` means the file holds
  the name as a whole word. `excerpt` gives the test's own lines, after the file's opening lines.
  `remove` cuts the test, with its decorators, out of its file.
- **WS-17** `Output.key_line` gives the last exact error line, else the first failure-like line,
  else the last line. Banners are skipped. `signature` is the same for failures that differ only in
  numbers, times and addresses. `failure_text` keeps the end of the output, within the limit.
  `compact_lines` shortens very long lines to their two ends.
- **WS-18** `Output.misses_own_code` names the project's own module that a test could not import.
  `unloadable(output, path)` is true when a syntax or parse error is located in that file.
  `mentioned_in(output, paths)` lists the files the output names, the most named first. A file
  name inside another file's name does not count. `tool_failure` gives the key line without the
  project's path and numbers.
- **WS-19** `FileView(...)(limit)` gives the fixed parts, then each candidate file whole while it
  fits, then a numbered window (up to `windows` of them) around the line the output points at,
  then the tail parts. A how-to part comes with the windows. `shown` is the files shown whole plus
  the files shown in windows.
- **WS-20** `Change` is true when it changed a file. `files` lists the changed files, and `created`
  lists the new ones.
- **WS-21** `online()` is true when one of the probe addresses accepts a connection.
- **WS-22** A window holds the lines on both sides of the line it is about, as far as the room and
  the file allow: never only the lines from it downwards while lines above it fit.

## model

- **MOD-1** `Models.settings(name)` is what calibration measured for a model, kept in the home's
  `models` under the model's name, or `None`.
- **MOD-2** `Models.selected()` ends with `SystemExit` when no model is in use, when the model in use
  was never measured, or when a measured value (`max_prompt_tokens`, `num_ctx`, `max_reply_tokens`,
  `fix_max_tokens`) is missing, below 1, or a boolean. Otherwise it gives `(name, settings)`.
- **MOD-3** `Models.installed(name)` ends with `SystemExit` when Ollama does not answer, or has no
  model of exactly that name. In that case it suggests close names. Otherwise it gives the name.
- **MOD-4** `Models.context_for(budget, reply, length)` is `budget + reply + 512`, rounded up to a
  multiple of 1024, and at most the model's context length. `Models.reply_limits(reply)` sets the
  longest fix to `min(4096, reply)`.
- **MOD-5** `Models.remember(model)` keeps the chars per token the run learned, when it learned one
  that differs from the one kept.
- **MOD-6** `Ollama.is_oom` recognises "out of memory" and "insufficient memory". `is_crash`
  recognises the runner's crash messages (runner failed or terminated, panic, killed, segfault).
- **MOD-7** `Ollama.get_json(path)` gives the JSON Ollama answers with. It gives `None` when Ollama
  does not answer or answers with an HTTP error. With `errors=True`, an HTTP error gives the error's
  own JSON, or `{"error": "HTTP <code>"}`.
- **MOD-8** `Ollama.chat` streams a request with `model`, `messages`, `stream: true` and
  `keep_alive`. It sends `think` only when it is set, and `options` only when there are some. It
  gives the final chunk, with `message.content` and `message.thinking`. An HTTP error is an
  `OllamaError` flagged out of memory or crashed when it is either. An unreachable Ollama is an
  `OllamaError`.
- **MOD-9** `Stream` collects the reply and keeps the last `THOUGHT_KEPT` chars of the thinking. An
  error chunk is an `OllamaError`: a reply error with what came before, unless it is out of memory
  or a crash. Once `done_when(content)` says the reply is complete, the stream ends within
  `EARLY_STOP_GRACE` seconds, and the reply is the content as it was at that moment. While it goes,
  its live line (`say.live`) says what the model is doing. A reply whose last `LOOP_LINES`
  finished lines are one non-blank line, again and again, is stopped there and is cut (`length`).
- **MOD-10** `Calibrator.shared(context)` sets the prompt budget to `min(8192, max(512, context // 3))`,
  and the longest reply to `max(1024, min(16384, context - budget))`. `safe_ratio()` is
  `CHARS_PER_TOKEN` until measured, and then 97% of the measure, within [2, 4], to 2 decimals.
- **MOD-11** `Calibrator.capacity(target)` gives `(target, None)` when the target reads. Otherwise it
  halves the gap until it is within `CAPACITY_STEP`, and gives `(largest that read, smallest that
  failed)`. Below the smallest usable context it ends with `SystemExit`.
- **MOD-12** `Calibrator.check_fit` ends with `SystemExit` when the weights and `MEMORY_OVERHEAD` are
  more than the GPU memory. With either size unknown, it checks nothing.
- **MOD-13** `Calibrator.run()` measures the model and keeps, in the home's `models`, a prompt budget,
  a context, a longest reply, a longest fix, a think setting and chars per token.
- **MOD-14** `Model` takes its limits from the measured settings. It builds at `build_think` only
  when the model thinks. Its chars per token stay within [2, 4]. `limit_chars(system)` is
  `tokens × PROMPT_HEADROOM × chars_per_token − len(system) − 40`.
- **MOD-15** `Model.ask_once` adds `NO_TOOL_CALLS` to the system prompt. It fits the user message to
  the prompt budget, gives the reply's content, and logs the exchange to its log.
- **MOD-16** A reply cut off while it is still thinking is asked again: first with twice the budget,
  up to the cap; then, if `unthinking_retry`, without thinking, with the thought so far to
  conclude. Otherwise the result is `""`, with `overthought` set and `last_thought` kept. A cut
  reply is finished, and kept, when its `done_when` says so or its blocks have all ended.
- **MOD-17** A failed request is tried again after what the failure calls for. Out of memory: a
  smaller prompt budget, up to `OOM_RETRIES` times. A stall: the model is reloaded, up to
  `STALL_RETRIES` times. A rejected think level: `true`, then no think setting. A reply error: one
  more try, then what came before it. A crash: the same call again; after a second crash near a
  limit, that limit is lowered for the run; after `CRASH_RETRIES` crashes it raises. Anything else
  is raised.
- **MOD-18** `Model.ask` tries a prompt that is too large again with every part relaxed. It raises
  `PromptTooLarge` only when even that does not fit.
- **MOD-19** `Model.ask_until_usable(…, read)` asks again when `read` rejects a reply, adding a note
  with the reason. It gives `None` once the attempts run out.
- **MOD-20** `learn_chars_per_token` keeps the lowest ratio of the last 20 prompts over 50 tokens,
  within [2, 4]. `learned_chars_per_token()` is `None` until 5 prompts have been seen.
- **MOD-21** In exclusive mode, every other loaded model is unloaded before a call. With the model
  kept loaded, it is unloaded once it grows past `max_growth` times its first size.
- **MOD-22** A calibration probe that fails is recorded as crashed, and the runner gets time to free
  its memory before the next one. When Ollama itself stops answering, calibration ends with
  `SystemExit`.
- **MOD-23** Calibration's first chat finds the think setting the model takes: `"high"`, else `true`,
  and none for a model that does not think. A model that does not run ends calibration with
  `SystemExit`.
- **MOD-24** `Model` keeps each call's step and details in its log and never prints them. The person
  sees only a short line when it asks again, and its live line says in plain words what the model is
  doing (reading, thinking or writing its answer) and for how long ("1m 41s (101s)").

## project

- **PRJ-1** `Runs.new_record` gives `{id, task, workdir, spec, created, status: "running"}`, with a
  6-hex-digit ID. `Runs.save` sets `updated`, keeps the record in the project's database, and keeps
  in the home's `projects` only a pointer: the run ID, the folder and the task.
- **PRJ-2** `Runs.find(name)` finds a project by its full run ID or by its folder's name, exactly,
  and never by part of either. No match ends with `SystemExit`, and so do two projects in folders of
  the same name (it lists their IDs). `Runs.entries()` gives each pointer, with the project's own
  record when its folder is still there.
- **PRJ-3** `Runs.workdir(name, folder)` gives the named project's folder, and its absence ends with
  `SystemExit`. Without a name, it gives the given folder, resolved, which must exist.
- **PRJ-4** `Runs.open_new` refuses a folder that holds a run, keeps the spec file's text in the
  project's database, saves a new record and gives the mode `"new"`. `Runs.open_existing` marks the
  run running. It gives the mode `"resume"` with the saved spec text, or `"change"` with the
  feature's spec file.
- **PRJ-5** `Runs.clear_folder` refuses the home folder, the filesystem root, and any folder that
  holds Tinker's home. It keeps the entry that holds the spec file. Without `yes`, and not in a
  terminal, it deletes nothing. Otherwise it empties the folder and forgets the pointers to it.
- **PRJ-6** `RunChecks.before_start`. A new run needs an existing folder that holds no run, and an
  existing spec file when one is given. A saved run needs a valid phase in every cycle. `add`/`fix`
  need a project spec and no unfinished cycle. `amend` needs a cycle that waits for confirmation.
  `restart` reads the task and spec text before anything is deleted.
- **PRJ-7** `Project` keeps `criteria` (saved as `steps`), `cycles` and `files` in the project's
  database. `Project.load(folder)` reads them back. `open_cycle()` is the first cycle not done.
  `new_cycle` starts at the first phase of its kind. `new_criterion` numbers the criteria
  `C1, C2 …` (`B…` for a bug) from one counter. `cycle(id)` and `criterion(id)` give the one with
  that id, or `None`. `counts()` gives `(passing, parked, total)`.
  `save()` keeps the plan and writes `SPEC.md`.
- **PRJ-8** `criteria_of(path, steps)` gives the criteria whose test file uses the file, or whose text
  names the file (`name.py`, or `stem.` for code). A template's criteria are also those of the
  files that render it.
- **PRJ-9** `entry_files` gives the design's entry point first, then any file the run command names.
  `contracts(paths)` lists each file's contract lines. `register` adds new files without changing
  known ones. `dependencies(read)` gives the dependency files. `uses(path)` gives the files used,
  directly and one level down. `remove_file` deletes a file and drops it from the design.
- **PRJ-10** `SpecDocument` writes SPEC.md: the title, the run and language, a table of criteria
  with readable status, the commands (setup, activate and the rest, with aliases spelled out), each cycle (constraints, non-goals,
  interface check, external-service data), the design (files, roles, uses, contracts), and each
  criterion with its source and, unless it passes, its last error.
- **PRJ-11** `Brief.load` gives `""` for a change. It reuses the brief kept in the project's database.
  A spec that fits the spec prompt is used whole. A longer spec is condensed chunk by chunk with the
  model.
- **PRJ-12** `Outcome` starts as `stopped` with exit code 1. `done` and `awaiting` exit with 0, and
  `blocked` exits with 1.
- **PRJ-13** `Tracker` is a run's one report. It keeps the title, run ID, folder, phase, steps done,
  passing spec points, what the model is doing now, the latest message said, a plan that waits, the
  end, the seconds taken and when it went on (`resumed`). Each change is published at once in the
  home's `running` under the run's process, and appended to the project's `progress` log.
- **PRJ-14** The steps are named by phase (`STEPS`: what each phase is doing, and when done). The
  passing count is taken from the project on every save, for every criterion (`C…` and `B…`). A
  plan that waits is kept as data: its project, run, language, how to start it, its kind, what
  changed, its criteria, its non-goals and its folder. The end is kept as data: its status, its
  headline, its details and the commands to go on.
- **PRJ-15** A run keeps every model call and every detail in the project's `events` log, and every
  message it says in its `output` log (its tracker follows `say`).
- **PRJ-16** `Runs.remember(run, language)` keeps one line about the run in the home's `runs`: `{id, task,
  language, status, created, updated}`, at most `RUNS_KEPT`, the least recently updated dropped
  first. `tinker clear runs` deletes these lines with the pointers.

## knowledge

- **KNW-1** `Library.slug`: "Test-driven development" gives `test-driven-development`, "C++" gives
  `cpp`, "C#" gives `csharp`, and "Python 3.14" gives `python-3-14` (at most 60 chars). `read(slug)`
  gives `None` for a topic that is missing or unreadable. `topics()` gives every readable topic.
- **KNW-2** A language has one identity, `language_key(name)`: its first word, without a version
  ("Python 3.12" and "Python" are both `python`). `Library.language(name)` finds a language learned
  under any version of its name. A language is learned and kept under its key, with its version
  apart.
- **KNW-3** `Library.items(topics)` gives one item per text, as `{topic, name, phase, text}`: the
  summary, the tools, what is installed, research findings, and each fact with its confidence. A
  broken topic is left out, with a note. `shown_in(item, phases)` sends machine items to the
  toolchain and config prompts, tools also to the tests prompt, findings to the spec and design
  prompts, and a fact to its own phase.
- **KNW-4** `language_key`: "Python 3.12" gives `python`, "JavaScript (Node.js)" gives `javascript`,
  "C++" gives `cpp`, and "C#" gives `csharp`. A language never seen starts empty.
- **KNW-5** `add_lessons(new, confirmed)` confirms known lessons by number. It confirms instead of
  adding a lesson that is like a known one. It skips a lesson with no text, an unknown phase, or one
  that names the project (`names`). A new lesson is a candidate. A lesson goes to the language or to
  any language by its scope. A store keeps at most `MAX_LESSONS`, and new lessons always stay. It
  gives how many were new. `lessons(phases)` gives the best evidenced first: the times seen plus the
  times helped, then the most recently seen.
- **KNW-6** Toolchain fixes. `known_fix` replays only a fix for exactly this failure, not tried yet.
  A replay that fails retires that fix at once. `remember_fix` keeps the last 20, one per failure.
  `similar_fix` needs a similarity of at least `SIMILAR_FAILURE`. `remember_toolchain` keeps the
  toolchain without its `run` and `check`.
- **KNW-7** `Embeddings` keeps each text's vector by its key, per embedding model, in the home
  database. Only missing texts are asked for, in batches of `BATCH`. A wrong reply gives `None`, and
  `failed` says why. `prune` keeps only the texts asked for. `cosine` is the cosine similarity, and 0
  for a zero vector.
- **KNW-8** `Retriever.by_words` ranks the texts that hold the query's words with BM25, best first.
  `rank` fuses the word and meaning rankings and gives every index: the ones neither finds come
  last. When embeddings fail, the meaning search is turned off for the rest of the run.
- **KNW-9** `Learner.safe_commands` drops a command with a shell operator, a command that changes
  something, and an unsafe command.
- **KNW-10** `Learner.read_facts` keeps facts of a known phase, cut to `FACT_CHARS`, at most
  `FACTS_PER_CHUNK`. `add_facts` keeps a repeated fact once and adds the other page to its `also`.
  `relevant_chunks` gives the page's first chunk and then the best-matching ones, in page order.
  Facts about unreleased versions are dropped.
- **KNW-12** `look_at_machine(commands)` runs each command in an empty temporary folder and keeps
  its first line and version. A failing command means the tool is not installed.
- **KNW-13** `Researcher.read_frame` and `read_analysis` keep only well-formed questions, findings
  that cite known facts, contradictions of at least two facts, and gaps and checks within their
  limits. An analysis with no finding is a `ValueError`.
- **KNW-14** `Researcher.cross_check` folds repeats and adds support from other sites when the
  numbers agree. When they disagree, it returns the pair. `supported(stated, cited)` never rates a
  finding above what its facts allow: high only when at least half the cited facts are corroborated
  and none is disputed. `entry` sets each fact's confidence to disputed, corroborated or single.
- **KNW-15** `Researcher.research(topic)` frames the topic as questions, reads pages for each,
  cross-checks and analyses the facts, searches the gaps and checks in one more round, and keeps the
  topic as research: its questions, findings, contradictions, open gaps, and facts with their sources
  and confidence.
- **KNW-17** Every lesson and every learned fact or finding has an ID and a status, `candidate` or
  `proven`. `prove(used)` makes the used candidates proven, and copies the used web facts into the
  language's knowledge as proven facts. `disprove(used, phases)` retires every used item shown in
  those phases' prompts, whether it was proven or not.
- **KNW-18** `facts(phases)` gives the language's proven facts for the prompts of those phases.
- **KNW-19** `Knowledge(language, run)` records each item's history as `{event, run, at}`, the last
  `HISTORY_KEPT`: a new lesson or fix is `born`, a lesson seen again is `confirmed`, every item a
  succeeding cycle used `helped` (counted in `helped`, proven before or not), and a retired one
  `failed`. A replayed fix that works `helped`.
- **KNW-20** A retired item leaves the lessons, facts, findings or fixes it was among for the
  `retired` list beside them, with status `retired`, its `reason` and the run. That list keeps the
  last `RETIRED_KEPT`. Nothing retired is shown in a prompt.
- **KNW-21** `Library.save` gives each research finding `cites`: the IDs of the facts it cites by
  number, so the link holds when a fact is retired.
- **KNW-22** A topic's playbook. `condense(plan, facts)` asks one call to turn the topic's facts (a
  language's with the lessons its own projects proved; candidates are left out) into at most
  `PLAYBOOK_RULES` rules, at the think setting it is given (learning's `learn_think`). `read_rules` keeps a rule only with a known phase and at least one cited fact or lesson,
  cuts its text to `RULE_CHARS`, keeps one per text and gives each the IDs it cites. When the model
  gives no usable rules, there are none and the facts are shown as they are. `Library.items` gives
  each rule (kind `rule`), and the rules stand in for the facts: none is shown but those a retired
  rule cited and no rule in use cites. A rule is a claim like a fact: it is proven and copied into
  the language's facts, or retired.
  Each rule says when it applies (`when`, at most `RULE_WHEN_CHARS`): "" for every program, else
  the kind of code it is for, which a proven copy keeps. What the reply says a new project declares
  to get the newest version (`declare`) is the first rule, for every program.
- **KNW-24** Learning a language again (LRN-11) replaces what its earlier learning proved: `supersede` retires
  the language's proven facts from its own topic that the new learning does not show (its rules,
  when it has them), with the reason that they were replaced. Proven facts from other topics stay.
- **KNW-25** `missing_checks` names the language's learned tools that check code and the toolchain that
  worked does not run: a type checker it never names, a linter or formatter where it has no command.
  `toolchain_title` asks to add them. `unavailable_tools` names the learned tools not installed here
  that the toolchain neither installs nor runs, and that are not to be added.
## learning

`tinker learn TOPIC` and the recall phase of a run with `--web` learn a language or a tool by its slots
(`SlotLearner`), from its own documentation for the version installed: PREFLIGHT, PLAN (saved; else SEARCH 1,
GATE 1, PICK, FETCH, GATE 2 and the plan call), MACHINE, DOCS, GATE 2, CLEAN, SELECT, DIGEST, FOLLOW-UP,
CONDENSE, KEEP. Domain research (`Researcher`), own-command learning and run lessons are apart.

- **LRN-1** Profiles are data. `PROFILES["language"]` is the slots installed, toolchain, fundamentals,
  this-version, limitations, best-use-cases and readiness; `PROFILES["tool"]` is installed, purpose,
  core-usage, this-version, pitfalls-and-limits, conventions, fit and readiness. Each `Slot` has an id, a
  question, a query hint, a passage count, a minimum, the section (fact phase) it fills, its source
  (`web`, `machine` for installed, `check` for readiness), and its `docs` words: what a documentation
  site's addresses and titles call its pages (`whatsnew`, `changelog`). `TOOL_DOCS` are those of a
  language's tools' pages. `asked(plan, version)` is its question with the
  plan's hint; `search(plan, version)` is the plan's follow-up search for it, else its own query.
- **LRN-2** `Throttle(rate, backoff)` lets at most `rate` requests a minute through (a token bucket, full
  at the start; an empty one waits). `call(request, limited)` asks again while `limited()` says HTTP 429,
  after pauses of `BACKOFF_FIRST` seconds, doubled, and raises `RateLimited` once `backoff` seconds have
  passed since the first 429: it never waits longer than that in all.
- **LRN-3** `RequestLog` appends every web request of a learn to `Home.web_log()` as one JSON line
  `{at, kind, result, seconds, target}` (kind search, fetch, robots, probe or fallback; result ok, 429,
  missing or failed: a robots.txt, site map or version's documentation the site does not have is
  missing). It counts them by kind, and `text()` gives them in one line with the HTTP 429 count.
- **LRN-4** `Fetcher.fetch_all(urls)` gives a `Fetched` per address, in order: status, final address,
  text, content type and error. The pages of one site go one after another over one kept-alive connection
  (`http.client`), the sites in parallel (`workers`); a connection the site closed is opened again once.
  One page failing never stops the others. It sends its User-Agent, asks for gzip, reads at most
  `max_bytes`, follows at most `MAX_REDIRECTS` redirects, waits `domain_delay` between two requests to one
  site, and decodes by the header's charset, else the page's `<meta>` charset, else UTF-8. An address the
  environment sends through a proxy is fetched with urllib, which honours it.
- **LRN-5** `Fetcher` honours each site's robots.txt (read once per site): a disallowed page is not
  fetched, and its `Fetched` is `refused`. A missing robots.txt allows everything; one answered with 401
  or 403 allows nothing.
- **LRN-6** `Cleaner.page` takes an HTML page's main content as blocks (`<main>`/`<article>` when it holds
  `USABLE_PAGE_CHARS`, else the body), leaving out what `PageText` leaves out and every paragraph that is
  at least `LINK_ONLY` links; plain text and Markdown are read by their lines (`#` headings, ``` code).
  `Cleaner.pool(pages, known)` gathers blocks under their heading into passages of up to `PASSAGE_CHARS`
  (a code block whole), drops text a site repeats on several of its pages and a passage that repeats
  another or a known one (`NEAR_DUPLICATE`), and numbers them on from the known ones (`P1`, `P2`, ...),
  each with its address and fetch time.
- **LRN-7** `Selector.select(slots, pool, plan, version)` gives each slot its passages from the one pool:
  its best `SLOT_CANDIDATES` by the words of its question, the topic and the version are shortlisted, and
  only those are embedded; of them, only those that hold a keyword of its own search (`Slot.keywords`,
  without the topic) or are close to its question in meaning (`SLOT_RELEVANT`) count. They are ranked by
  words and meaning fused, taken by MMR so a near-repeat of a passage taken is passed over, at most the
  slot's count and within the digest's room. A passage may serve several slots.
- **LRN-8** `short_slots(profile, selected)` gives the web slots below their minimum, the emptiest first.
  Readiness, `missing(profile, facts, machine)`, gives installed when no version command ran and each web
  slot without a fact.
- **LRN-9** `read_plan` needs a kind (language, tool or domain) and a name. It keeps safe commands only
  (`MAX_COMMANDS`), its documentation site (`docs`: web addresses only, `versioned` only with `{version}`
  in it), a language's tools (one per role of `TOOL_ROLES`, each with a safe version command and its
  `docs`), and hints and follow-ups for its profile's slots only; a tool's language is `used_with`. `read_digest` keeps a fact only when it cites a
  passage given and speaks of no unreleased version; each keeps its slot, the passages it cites, their
  pages (`source`, `also`), and a known phase (else the slot's section); at most `FACTS_PER_SLOT`.
- **LRN-10** Fail fast. No model call (plan, digest, condense or embedding) runs until the web, or the
  documentation kept from before, has delivered usable pages. PREFLIGHT stops when the Internet plugin is
  off or there is no embedding model; GATE 1 when the search fails, is rate limited past `learn_backoff`,
  finds nothing, or finds nothing above `learn_cutoff`; GATE 2 when fewer than `learn_min_pages` pages give
  usable text (snippets never count). With a saved plan, GATE 2 counts the documentation's pages, and runs
  search 1 when they are too few. Then it raises `LearnStopped` (a `SystemExit`: a non-zero exit) naming
  the cause, what was tried and the requests made, saying nothing was learned or saved and what was learned
  before is untouched, and the next step; nothing is written to Tinker's database.
- **LRN-11** A first learn makes one search (`FIRST_SEARCH`, from the topic alone), picks the results whose
  title and snippet score above `learn_cutoff` (BM25), each address once, at most `learn_pages`, and
  fetches them with the plugin's fetcher. A page that gives too little text is fetched by the plugin
  instead (logged as a fallback), at most `fetch_fallbacks` per learn, never one robots.txt refused. Only
  then does the plan call run (at the model's own think setting); it names the documentation site and,
  for a language, each tool's version command and documentation. A saved plan is reused with no search
  and no call. The version commands (each run once) give the version installed; the documentation of that version (LRN-14
  to LRN-17) and of a language's tools (`TOOL_DOCS`, at most `tool_docs_pages`) joins the pool. Each slot's
  passages are digested into facts (candidates, each with its slot) and condensed into rules, both at
  `learn_think`, and kept only after both succeed, under the language's key or the topic's slug, with its
  plan, machine, documentation versions, sources, cited passages, status (`complete` or `incomplete`),
  missing slots and request counts; then indexed. A summary prints the slots filled and missing, the
  documentation, the pages by source, the facts and the requests by kind.
- **LRN-12** After the digest, each slot left without facts gets the plan's follow-up search, while fewer
  than `learn_searches` searches were made; its pages join the pool, and that slot alone is selected and
  digested again. When a follow-up fails, the learn goes on, marked `incomplete`, with a warning and a note.
- **LRN-13** A topic researched before is researched again when deep, else kept. A plan that says the topic
  is a domain hands it to `Researcher` when deep; otherwise it is learned like a tool.
- **LRN-14** `DocSite.resolve(docs, version)` tries the version's documentation most exact first
  (`version_keys`: 3.15.1, 3.15, 3), at the plan's `versioned` address, then the unversioned `root` as
  "latest", and gives the first root page that answers with its version key.
- **LRN-15** `DocSite.map(root, base)` lists the site's pages from its `llms.txt`, else its `sitemap.xml`,
  else the root page's own links (`Links`), on its site and under the root (all of the site's when none
  is under it), at most `MAX_MAP_LINKS`. `pick(links, wanted, key, cap, base)` gives each slot its pages
  by the words their address (below the base) and title share with the slot's `docs` words, in turn (each
  slot's best, then each one's second), a page that names the version first, each once, at most `cap`.
- **LRN-16** `DocSite.read` fetches the root and the picked pages, then picks the same way from the links
  of the pages it read, one level on (the version's what's new behind an index), at most `docs_pages` in
  all. A page that names another version (`whatsnew/3.14.html` for 3.15) is never picked; the latest
  documentation is matched to the version installed (major.minor). Pages left with no text are dropped.
- **LRN-17** `DocCache` keeps the cleaned pages in Tinker's `sources` as `slug@version`. `DocSite.pages`
  reads them from there before the web, so learning a version again fetches none of its documentation. A
  copy of the "latest" documentation is read again after `docs_latest_days`; a version's own is kept until
  `tinker clear docs`.
- **LRN-18** Given a tracker, learning moves it through its steps (`Tracker.STEPS`: search, plan,
  machine, documentation, digest, follow-up, condense, keep; a domain's frame and investigate), each
  once in a row; without one (a run's recall phase), the run's own steps are left alone. The summary
  is kept as data too (`recap`: answered, still open, documentation, pages, facts kept, web requests),
  and a `LearnStopped` carries why it stopped and what to do next (`details`).

## graph

- **GRF-1** `Graph.build()` makes one node per thing Tinker knows: each language (one for its
  knowledge and what was learned of it), any language, each topic learned, the person's commands,
  and each lesson, fact, finding, toolchain, fix, command and retired item. A node is `{id, kind,
  label, text, status, group, details, sources}`; `group` is the language or topic it belongs to. A
  fact's details name its slot, and a topic's details name the slots it is missing.
- **GRF-2** Links: `belongs_to` from an item to its language, topic, any language or the person's
  commands; `cites` from a finding to its facts and from a language's rule to its facts and lessons; `became` from a web fact to the language's proven
  fact it was copied into; and `taught_by`, `confirmed_in`, `helped_in` and `failed_in` from an item
  to the runs in its history. A run is a node with its line from `runs`, or marked as having none.
- **GRF-3** `similar` links join lessons by meaning (cosine at least `SIMILAR_MEANING`) when the
  embedding model gives their embeddings, else by words (at least `SIMILAR_WORDS`). `similar_by`
  says which, and why words were used.
- **GRF-4** `GraphPage.html(graph)` is one self-contained page: the graph as JSON inside it, and no
  script, style, font or image from elsewhere. It holds a plain list of every node by group (readable
  without scripts), escapes every text, and draws the graph when scripts run.
- **GRF-5** `tinker memory --graph` writes the page to `Home.graph_page()`, prints what it holds (the
  counts, and how lessons were found similar), and opens it in the browser only when printing to a
  terminal.

## reading

`tinker read FILE | URL` reads what the person gives: a book, an article, a document. Its whole text is stored first
(no model), numbered on the reading list, so it is never sent twice; then every chapter is learned from, the core ones
first, and saved as soon as it is read. What it teaches is a topic of the library (kind `reading`, slug `read-N`);
its text stays for ask, teach and a focused read again (`read #N "WHAT"`).

- **RDG-1** `opened(data, name)` reads a source by what its bytes hold, never by its name: a zip is an EPUB, other
  text is Markdown or plain text (its title: a lone top heading, else the file's name). A PDF is refused, saying to
  convert it to EPUB first (Calibre); a web page is refused, saying a share link needs its download link; any other
  binary file is refused.
- **RDG-2** An EPUB is read in its spine's order. Its contents (EPUB 3's navigation document, else EPUB 2's NCX) mark
  where each chapter starts (an entry's fragment, else its document's start), at the shallowest depth that gives
  `READ_CHAPTERS` chapters (else the deepest); what comes before the first is the opening pages. Its title and author
  come from its metadata. An EPUB with no usable contents is split at its headings.
- **RDG-3** A Markdown text is split at its headings, at the depth `chapter_depth` gives (a lone top heading is the
  title, not a chapter). A plain text is split at its chapter lines ("Chapter 3", "Preface"), else into parts of
  `READ_WINDOW_CHARS`. A piece that holds no more than its heading is no chapter.
- **RDG-4** `kind_of(title)` gives a chapter's kind: `index`, `raw` (front or back matter: contents, copyright,
  acknowledgements ...), `front` (foreword, preface, introduction ...: what the source says about itself) or `body`.
  `index_terms(book)` gives the index's terms with how many pages each refers to, the most first, at most
  `READ_INDEX_TERMS`.
- **RDG-5** `stored(source)` stores the source's whole text before anything is read: on the reading list
  (`Readings`, the home's `readings`) as the next number, with its title, author, source, the fingerprint of its
  text, the day it was added and each chapter with its text, kind and state (`unread`; `raw` for an index and front
  or back matter: kept, never read). A text stored before is that one: it says its number, and nothing else is done.
- **RDG-6** The survey, made once per reading and kept with it, sees the contents (each chapter's kind and length),
  the front chapters and the index terms. `read_survey` needs a summary; at most `READ_TERMS` terms; `software` is
  true only when the reply says so; each chapter is `core`, `supporting` or `other` (left out: other). It orders the
  reading and never skips a chapter: `in_order` gives the core ones, then the supporting, then the others, each in
  the source's order.
- **RDG-7** `Reader.read(N)` reads every unread chapter in that order, each in parts that fit the prompt
  (`READ_TEXT_SHARE`), one call per part, each shown what was learned from the reading before (the latest kept when
  the prompt is full). Each chapter is saved as soon as it is read: its new facts (one already kept is not kept
  again) join the topic, each with its chapter, and it is marked read; so a reading stopped goes on with the
  chapters not read yet. With every chapter read, it says so and asks nothing.
- **RDG-8** `read #N "WHAT"` reads again the chapters WHAT names ("chapter 7"), then those most about it by its
  words, `READ_FOCUS_CHAPTERS` in all, never a raw one: each shows its opening and its parts most about WHAT
  (`excerpt`, `READ_CHUNKS` parts), and the prompt says what the person asks to look for. What it finds that is new
  joins the topic.
- **RDG-9** `read_points` keeps at most `READ_POINTS` points, each cut to `FACT_CHARS`. A point of a source about
  building software keeps its phase when it is one of `FACT_PHASES`; every other point has the phase `book`, which
  no build is shown. A reply with no list of points is not usable. Once every chapter is read, a source about
  building software has its facts condensed into rules (again when a reading found something new); no other is.
- **RDG-10** `book_bytes(source)` reads a file, or downloads a web address with `Internet.download` (the plugin's
  fetcher; no web backend needed), at most `READ_MAX_BYTES`; a missing file, a failed download or a larger one is an
  error that says so. `Fetcher.download` gives the bytes as they are, through the environment's proxy, and refuses
  more than its limit.
- **RDG-11** `Readings.about(text, items)` gives the readings the text names by title, then those whose items were
  shown. `passages(numbers, query)` gives their passages (`PASSAGE_CHARS`) most about the query by its words, at
  most `READ_PASSAGES`, each with its title and chapter.
- **RDG-12** `tinker read FILE | URL` stores the source, then learns from it as `read #N`: its job's arguments become
  `read #N` (`Jobs.retarget`), so a stopped or paused reading goes on with what is not read, and nothing is fetched
  again. `read #N ["WHAT"]` needs a measured model and a known reading. Its tracker moves through survey, chapters
  (and condense) and ends with what was read now, the chapters read, what is kept, whether builds use it, and what
  to do next.
- **RDG-13** `read list` shows each reading, the newest first, with how many chapters are read and the facts kept;
  `read show #N` its source, summary and each chapter with its state and facts. Neither is a job; in a chat they are
  answered at once. `clear read [#N...]` deletes those readings (every one with no number): their text and their
  topic, once the person agrees.

## teaching

`tinker ask QUESTION` answers from what Tinker keeps. `tinker teach WHAT` corrects it, and nothing changes until
what was taught is checked: the model turns it into claims and the change it asks for, the claims are checked
on this machine, on the web and in what Tinker read, the model weighs that evidence alone, and only then is
anything changed.

- **TCH-1** `Memory(embeddings).items` are the memory graph's toolchains, fixes, lessons, facts, findings, rules
  and commands, without the retired. `about(text)` gives at most `MEMORY_SHOWN` of them, the most related first:
  first the items whose kind and language (or topic) the text names. A language's toolchain counts as named with
  its language, however the text calls it ("toolset").
- **TCH-2** `Asker.answer(question)` makes one model call with the items numbered, and says the answer alone:
  no steps and no sources, then the time taken (a terminal shows one status line while it thinks). With nothing kept it
  makes no call and says so. It changes nothing.
- **TCH-3** `read_plan` keeps at most `TEACH_CLAIMS` claims, each a fact or a preference (the person's own
  choice). A preference is only what the person marks with the word prefer: a teaching without it (`marked`) has
  facts alone, whatever the model says. It keeps at most `TEACH_COMMANDS` commands and `TEACH_SEARCHES` searches,
  and the changes: each rests on the claims it names and retires items shown, replaces toolchain commands of
  `TOOLCHAIN_KEYS`, keeps a lesson with a known phase or keeps a fact in place of an item shown (**TCH-9**; any
  other is dropped). Setup given as one line is split at `&&` into its commands; any other
  command given as a list is joined with `&&`. A plan with no claim, facts that nothing checks (no command, no
  search, and no reading it is about), no change, a
  toolchain key in two changes, or a toolchain key or value it cannot use is a `ValueError` (the model is asked
  again with why).
- **TCH-4** `Teacher.teach` checks the facts first. It runs only the commands that show what is installed (each
  other one is skipped, and said; a tool not found here may be in a project's own environment), and reads the
  pages the searches find. A preference is honored, never checked: with only preferences, nothing is run or
  searched and the judge is not asked. Facts with no evidence are unsure, and the judge is not asked.
- **TCH-5** `Verdict.read` needs a verdict for every fact (`holds`, `wrong` or `unsure`; one that holds with no
  evidence it can cite is unsure), and whether each change a fact is for follows. Each change is made on its
  own: when every claim it rests on holds or is a preference, and it follows. Each other one is said with why.
  When no change is made, nothing changes.
- **TCH-6** A change that is made retires the items it shows wrong (the reason names what was taught), replaces
  the commands of the language's toolchain (only when it has one, and the result is usable), and keeps its
  lesson (or its fact, **TCH-9**) as proven, with a `taught` event and its sources: the evidence its facts cite,
  and "your preference" for a preference. A lesson like a known one proves that one instead of being added.
- **TCH-7** `tinker teach` needs the Internet plugin, checked before any model call. Both commands report through
  a tracker, and in the conversation they run as tasks.
- **TCH-8** An ask about a reading (named by its title, or whose items are shown) also sees its passages most about
  the question (`Readings`), so it answers from the source's own words. With nothing kept and no passage, no
  model is asked.
- **TCH-9** A teaching about a reading is checked against its text: its passages most about each fact are evidence,
  and a plan needs no command or search for its facts. A change may keep a `fact` in place of an item shown (`about`):
  that item is retired, and the fact kept in its topic as proven (`Library.taught_fact`), with its phase, chapter
  and section, a `taught` event and its sources.

## exercise

`tinker exercise LANGUAGE` practises a language on its own, to write better code faster: practice projects one
after another, each built and given a feature, measured, and turned into lessons, until the person stops it. The
person always comes first.

- **EXR-1** `identified(model, words)` gives a learned language's name with no call. Otherwise one call says
  whether the words name one language, could be several (`ambiguous`, with the likely ones), or none (`other`,
  with why). For anything but one language, it says so and ends (`SystemExit`, "Nothing done"): no build starts,
  no exercise is kept, and no folder is made. `read_verdict` needs a known verdict, and a language needs its name.
- **EXR-2** `knows(name)`: a language Tinker has not learned is learned first (`tinker learn`, as a build of the
  exercise). A language whose learned machine commands all failed is not installed here: it says so and ends
  before anything is practised.
- **EXR-3** `read_choice` needs a task and a feature, rejects a task like an earlier project's (`SAME_PROJECT`),
  adds the language to a task that does not name it, and makes a folder name from the task when the one given
  does not fit. `free_folder` gives a folder of the exercise folder (`EXERCISE_FOLDER` in the projects folder)
  that no project has yet.
- **EXR-4** `practise` goes through a practice project's steps, `build → feature → measure → reflect → done`,
  saving each as it ends, from the step it reached (a new project is chosen only when none is open). `build`
  runs `tinker task TASK --workdir FOLDER`, and `tinker resume --workdir FOLDER` once the folder keeps a run.
  `feature` runs `tinker add FEATURE`, and resume once its cycle exists, only for a project that built (`done`).
  `reflect` makes one call, keeps its lessons through `add_lessons` (as candidates; none that names the project),
  and keeps its `focus` for the next choice.
- **EXR-5** `measured(folder)` reads the project's own database: its status, its minutes (every session's wall
  time), its model calls, the retried ones (attempt 2 and later), reply tokens, spec points passing, parked and
  in all, README examples that failed, and the `slowest` steps (a step is its call's first word, so no file is
  named). `problems` lists, at most `EXERCISE_PROBLEMS`: what nothing made pass, failed README examples, failed,
  unusable and cut replies, and the warnings the runs said.
- **EXR-6** The person comes first, as with every job: the exercise is one (**JOB-8**, **JOB-10**). A job of
  theirs, a chat or a command line pauses it: its build is stopped as Ctrl-C stops it (it saves itself), and the
  exercise goes on from where it was once Tinker is idle (**JOB-9**). It has no rule of its own for giving way. A
  run an exercise started (its build) is marked `exercise` in its tracker and is not a job: the conversation leaves
  it out of what runs (`persons_runs`, `Desk.now`), and its status shows the exercise when nothing else runs
  (looked at every `STATUS_EVERY` seconds, so it moves on by itself). The exercise's tracker shows its build's: its
  step after the exercise's, what the model writes now, the spec points passing and the latest line, and a
  practice project's steps only. As a conversation starts (`adopt_exercise`), it keeps up the message of an
  exercise that runs, and an exercise that ended without being stopped (killed, the computer restarted) goes on;
  either way its message is taken out and sent anew at the bottom. While it listens, it adopts again an exercise
  that went on by itself.
- **EXR-7** One exercise runs at a time (`Exercises.claim`). It is a job: Ctrl-C or `stop #N` (the `STOP`
  signal) stops its build, keeps everything, and ends `stopped` with how to go on (`resume #N`, or
  `tinker exercise LANGUAGE`, goes on from there). A second exercise pauses the one that runs (**JOB-8**).
  `exercise status` shows each exercise: whether it runs, is paused or stopped, its focus, each recent
  project's measures, and what it does now. `tinker exercise` takes a language or `status`; `stop` and `resume` say they are now
  `tinker stop #N` and `tinker resume #N`. In the conversation, `exercise LANGUAGE` (or `resume #N` of an
  exercise's job) starts it as a tinker of its own that outlives the conversation, its progress in its one
  message: every exercise has the same one (its ids kept in the home), started, stopped (`stop #N` shows only
  there) or resumed, and the person's `exercise LANGUAGE` message is taken out; an exercise paused for another
  hands it its message, and gets it back when it goes on (the conversation adopts it again). An exercise that
  ended without being stopped goes on as its job resumed. One that ends before it keeps a
  tracker shows the last lines it printed. A conversation adopts an exercise whose tracker does not say it
  `reports` by itself (`Attached` says so).

## session

- **SES-1** `Session` is what every phase works through: the model, the project, its workspace and
  toolchain, the knowledge, the Internet plugin, the run's options (`web`, `confirm`) and its
  tracker. `Worker` and `Phase` take it, and nothing else, from the run.
- **SES-2** `Session.setup` runs setup only when the setup commands or config files changed (or
  when forced), and it prepares `.env`. `run_all_tests` runs the suite once per state of the
  project's files. `note` keeps the last 30 problems. `ask_until_usable` ends with `SystemExit`
  when no reply was usable. `format_code` drops a formatter that fails.
- **SES-3** `knowledge_parts(*phases)` gives lessons and learned facts as optional parts, plus the
  person's commands for the toolchain. It records each item it shows in the cycle's `used`, with the
  prompt phases it was shown in. A prompt that writes code or tests is also told the checks they must
  pass: the toolchain's lint command, when it has one.
- **SES-4** `Session.save` keeps the project and gives the tracker the passing count.
- **SES-5** `Session.knowledge` is for the project's language. Before the spec names it, it is for the
  language the request names (`Knowledge.named_in`: the first it names of the languages Tinker knows),
  and Python only when it names none.

## planning

- **PLN-1** `asked(cycle)` is the request, followed by each amendment the person made.
- **PLN-2** `Working.mistakes(text)` checks each chain of plain arithmetic (`=`-joined; `× ÷ − x`
  allowed). It reports each part whose value is not the chain's last value, within that value's
  rounding. A chain that goes on (`…`) may be truncated.
- **PLN-3** `SpecWriter.traced(source, request)` is true for `"setting"`, or when at least 60% of
  the source's words of 3 or more letters are in the request. Brackets in the source are ignored.
- **PLN-4** `SpecWriter.runs_program(text, entry, run)` is true when a criterion calls `main(` or the
  entry module, names the run command, reads as a command line that prints, or speaks of running,
  input, output or the exit code.
- **PLN-5** `SpecWriter.read_spec` drops a criterion not traced to the request, and needs at least
  one. It sends back wrong working (at most twice). It needs each test in a test file of its own, an
  entry that is one of the files, and (for the project itself) a criterion that runs the entry
  program and a toolchain without problems. A test file the plan does not list is taken as the one
  it lists with the same stem, or as the only one it lists. It defaults the language to Python. It
  leaves out a planned settings file (`.env.example`) when no criterion is about a setting.
  The spec's prompt shows what was learned for the spec, the design, the config files (which it
  plans) and the toolchain.
- **PLN-6** `SpecWriter.take` adds the criteria, puts the files into the design in build order (a
  new project gets a `README.md`), sets the toolchain and the entry, keeps the cycle's non-goals,
  constraints and data, and marks the cycle specified. When a user starts a file but the toolchain
  has no run command, the toolchain is asked for again. A cycle already specified is not planned
  again. A plan the person will confirm is a quick draft (think `low`).
- **PLN-7** `SpecWriter.amend` replaces the cycle's criteria with the amended spec, numbered from
  where they began, records the amendment and what changed (`+ ~ -`), and leaves the spec as it was
  when the model gives nothing usable. The cycle then plans its design and review again.
- **PLN-8** `Designer.order` puts config files first, then templates and code, each after what it
  uses, and tests and docs last. `read_toolchain_data` needs a run command when a user starts a
  file, and rejects a toolchain that has problems. `give_templates_a_renderer` gives an unused
  template to the code file whose role names it, or else to the entry. `drop_adapter_criteria`
  removes the criteria about an adapter.
- **PLN-9** `ContractWriter.order` puts each code file after the code it uses. `needed` is true for
  a file another code file or template uses, or one that renders a template. A file nothing uses,
  with no criterion, that is not the entry, is a marker. `write` sets the contract and adds the
  files it says it uses.
- **PLN-10** `InterfaceReviewer` checks only the files that use two or more contracted code files.
  It applies the corrections, checks the changed files again, and keeps its findings in the
  cycle's `review`.
- **PLN-11** `Recaller`. With nothing learned and no web, it makes no call and uses no topic.
  Without the web, it uses the topics the request names or is close to. It keeps the chosen
  topics' facts in the project: tools and machine facts first, then a language's rules, then the
  other facts. The project's learned language is always used.
- **PLN-13** A topic is used when the request names it, by its name or by its language without a
  version. What the project does not need stays out of its prompts (`inapplicable`), proven or not:
  a rule for some code (its `when`) until the spec reply names that code among the listed
  `situations`, and anything that names a tool `unavailable_tools` gives.
- **PLN-12** With the web, recall makes one call that names the learned topics to use and up to
  `learn_missing` new topics to learn (one per slug). Those are learned first, and they come first in
  the cycle's topics. When the call fails, the cycle goes on without learned topics.

## building

- **BLD-1** `TestWriter` writes the missing tests, `TESTS_PER_CALL` per call. A test file that
  already holds tests is never rewritten: new tests go in a new file next to it (`test_x_2.py`),
  and their criteria follow them. A rewrite that loses a test is reverted. A corrected criterion
  updates the spec. A test that could not be written counts against its criterion.
- **BLD-2** `CodeWriter` writes the missing config files first, in one call. Then it writes one call
  per code or template file, in design order. A marker file is written empty, with no call. A new
  file goes where the design says. A file more than twice `MAX_FILE_LINES` long is asked for again,
  once. `wiring_problems` names each used file the code never mentions.
- **BLD-3** `Verifier.run` gives True when every criterion passes. A failing criterion gets fixes
  until they stop making progress, then a review against the plan. A criterion that still fails is
  parked, and its files go back to how they were. A criterion that fails the same way as one already
  parked is parked at once.
- **BLD-4** A run stopped in the middle of a criterion leaves a restore point in the project's
  database. The next run puts the files back from it. `failure_key` ignores the test's own name.
  `roll_back` also drops the files created since.
- **BLD-5** `FixAttempt`. A fix changes only existing files that are shown, and never a test. A
  review may change the test and add new files next to it. A reply of `TEST WRONG` blames the test
  and changes nothing. A change that breaks a passing test is reverted, and that test is kept.
- **BLD-6** `TestTool.ran_empty` is true when a test command, run before any test exists, exits 0,
  or says it found no test, or prints nothing that looks like an error. It is false for exit codes
  126 and 127 and for a timeout. `works()` is true when setup, the test command and the linter run.
  A linter that is not installed is a tooling problem.
- **BLD-7** `ToolchainRepair` replays a fix learned for exactly this failure, with no model call.
  Otherwise one model call may change only config files and the commands, and a fix that only made
  new files and commands is kept to replay.
- **BLD-8** `BuildFix` may change only the existing files it showed, and never a test.
- **BLD-9** `BugReproducer` needs a new test file, given as `<file>::<name>`, and a test that fails
  now. It writes the test for the report and each amendment (`asked`). A report no test can show is
  dropped with its test, and the cycle closes as not reproduced.

## finishing

- **FIN-1** `ReadmeWriter.examples(text)` gives `(command, expected lines)` for each `$ command` in a
  console block of the Usage section.
- **FIN-2** `ReadmeWriter.compare(expected, output)` ignores whitespace and skips lines with `...`.
  A `<placeholder>` matches any value, and the value is kept.
- **FIN-3** An expected line matches only a whole output line: `80.33` does not match
  `80.33000000000004`, and `7` does not match `17`.
- **FIN-4** `ReadmeWriter.read` needs features and at least one usage example. It keeps at most
  `MAX_USAGE` examples. A run command is served when the reply says so, or when an example talks
  to localhost.
- **FIN-5** `ReadmeWriter.text` writes: the title, the overview, the features, getting started (the
  `use` commands, how to run it), usage as console sessions, the settings (from `.env.example`), and
  development (setup, activate, tests, format, lint).
- **FIN-6** `Tidy.findings(output, path)` counts the problems located in a file. `said_about` keeps
  the output's blocks that name the file. `reported` lists the files with findings, the most first.
- **FIN-7** `FinalReview` passes only when: setup runs afresh, the whole suite passes twice, the suite
  ran tests, and every criterion's test is there and passes. Otherwise it says what is wrong.
- **FIN-8** `LessonWriter` proves the knowledge the cycle used, and counts a finished project and
  keeps its toolchain once. With no problems noted, it makes no call. Otherwise it keeps at most 3
  new lessons, never one that names the project's files, and clears the noted problems.
- **FIN-9** Every README example runs as a person runs it: after the `use` commands, with a server
  started first when the app serves. A failing example gets up to `FIX_ROUNDS` fixes, to the code or
  to the README. A fix stays only while every passing test still passes. What still fails is kept in
  the cycle's `readme_failures`.
- **FIN-10** `Tidy` gives each file the linter reports one call, with the file as numbered lines. A
  change stays only when the linter finds less in that file, every passing test still passes, and
  every test is still there.

## conversation

- **CNV-1** `Markup` renders `**bold**`, `*italic*`/`_italic_`, `~~struck~~`, `` `code` ``,
  `[links](https://…)`, fenced blocks, ` ```output ` blocks (folded), `- ` items (as `• `) and `#`
  headings: as plain text, as Telegram HTML (escaped), and as ANSI (plain when `NO_COLOR` is set or
  there is no terminal). `literal` escapes the marks. `of(template)` makes a message from a t-string:
  each value with its marks escaped, but `:md` keeps Markup as it is, `:code` makes a code span and
  `:block` a fenced block. Any other format is an error.
- **CNV-2** `Markup.columns(text)` joins the columns of a terminal table with ` | `. It leaves text
  that is not in columns as it is.
- **CNV-3** `pieces(text, size)` splits a message between lines into parts of at most `size` chars,
  and loses no text.
- **CNV-4** `command_of(text)` gives the command a message starts with (`/` allowed), by the command
  table. It is a command when the word is alone, when the command takes any words, or when it takes
  one word and exactly one follows. Otherwise the message is a chat (`""`). A menu's form is taken too:
  `/use_model` is `use-model`, and `@bot` after the name (in a group) is dropped.
- **CNV-5** `Channel.said` removes `tinker ` before a command in a conversation. `Channel.show` edits
  the thing's messages, sends parts that no longer fit, deletes the extra ones, adds the update
  time, pins when asked, and sends the message again at the bottom when it is no longer the newest.
- **CNV-6** `Channel.listened` gives only messages from allowed chats. It says a refusal once and
  gives nothing. `claim` lets only one process hold a channel's lock.
- **CNV-7** `ConsoleChannel` prints a dim headline while a thing changes, the whole message when it
  is done, and only the new lines when it grew. `exit` or end of input closes it.
- **CNV-8** `Progress(snapshot)` shows a run's tracker: its title, its steps done, the step it is on
  with its clock, what the model writes now, the passing spec points and the latest line (`text`);
  a one-line `brief`; how long it took (`took`); and its `ending`: the end's headline, details and
  next commands, or the plan that waits with its criteria and what to send next.
- **CNV-9** `Reporter` shows one run's tracker in one message per chat. It reads the tracker every
  `EDIT_EVERY` seconds and edits the message when it changed. It ends with the run's ending (with its
  `text`: a research's answer). A command that keeps no tracker shows its latest printed line.
- **CNV-11** `Talk.model()` is `None` until a measured model is in use. Otherwise it gives the chat
  options (no thinking for a thinking model). `reply` keeps the last `CHAT_KEPT` messages per chat,
  and it says plainly when no model is in use or the model failed.
- **CNV-12** `Shell.propose` gives one command line to confirm, with a warning when it changes or
  deletes things. It gives no command for an empty, multi-line or failed reply. `take` hands the
  proposal over once. `run` runs it in the person's shell and shows the exit code and output.
- **CNV-13** `Research.most_about` keeps the paragraphs that share the most words with the searches,
  in page order, up to `RESEARCH_PAGE_CHARS`. `searches` falls back to the topic itself.
- **CNV-14** `running(skip)` lists the runs whose trackers are in the home's `running`, but for its
  own process and those it skips. It removes the trackers of processes that have ended.
- **CNV-15** `Desk.answer` handles a command. `task WHAT` starts a task, and a bare `task` gets its
  usage. A run that starts a cycle is given `--confirm`, so its plan waits for the person. `status` alone gives what runs. `stop`, `stop #N` and `resume [#N]` act on jobs (**JOB-7**), and
  `research TOPIC` runs as a task. A task started while another runs pauses it (**JOB-8**). A command that deletes shows what it
  deletes and waits for `confirm`, but `clear chat`, which deletes no record (the history keeps it), is
  done at once. `exit` asks first; the person's `exit` and `confirm` are taken out at once, and the question as
  Tinker ends (its status says it no longer listens). A `channel` action that needs a terminal says so. Any other command
  runs as `tinker`.
- **CNV-16** `Desk.settle` runs what waits for a `confirm` (a deletion, a command line). Any other
  message cancels it and says so. `decide` builds or changes the plan that waits. For a new project,
  its first `confirm` asks for the folder's name.
- **CNV-17** `Desk.follow` follows a task by its tracker: its run ID, its folder (the title is named
  after it), and a plan that waits. Once the task ends, it reads the task's last state and removes
  its tracker.
- **CNV-18** Helpers. `json_in` gives the JSON object in a text, or `{}`. `first_lines` cuts lines
  and counts the rest. `first_words` collapses whitespace and cuts. `text_of` decodes bytes.
  `after_word` drops the command word. `built_project(name)` gives the project named (`Runs.find`),
  or without a name the one built last.
- **CNV-19** A chat or a command line said while a job runs pauses the job while it is answered
  (`Talk.pausing`, **JOB-10**); until the reply is written, its note says what is paused for it, and that it
  goes on once they have their answer.
- **CNV-20** `run [@PROJECT] ARGS [<<< INPUT]` runs a built project the way a person runs it: its run
  command, in its folder and environment, with the arguments quoted and the input given after `<<<`.
  The project is the one named, else the one built last. With no project built, or one with no run
  command, it says so.
- **CNV-21** `Desk.run` claims each chat channel. When another Tinker holds one, it stops if there is
  no terminal, and otherwise leaves that chat out. The terminal never waits on a chat: each chat starts in its
  own thread. Each chat has one status message, pinned: when Tinker starts listening, when it ends (`not
  listening`), and when the status is about something else (a task starts, waits for the person, ends), it is
  sent anew at the bottom and pinned, and the one before taken out; the same status going on (a task's
  progress) is edited in place. While it listens it collects the messages whose time is up, every
  `COLLECT_EVERY` seconds.
- **CNV-22** `one_shot` says one of the conversation's commands once, in the terminal (`tinker chat`,
  `command`, `research`, `run`, `history`). Each needs words after it, except `run` and `history`; `chat`
  and `command` pause the job that runs (**JOB-10**). `command` runs nothing without a yes at the terminal.
- **CNV-23** `research TOPIC` says when the web or the model is not usable. Otherwise it answers from the
  pages it read and lists them as numbered sources. It is a job: its tracker shows its steps (`searches`,
  `read`, `write`) and ends with its answer (the ending's `text`, which a chat's message shows).
- **CNV-24** A task run from a terminal reports to the enabled chats (`Attached`). `STOP` (SIGUSR2)
  stops it as Ctrl-C does (a job is paused with it too), and its last state is shown once it ends.
- **CNV-25** `/start` (what a chat app sends when a person first opens the bot) is answered with a
  welcome and a short, tldr-style guide for beginners in plain words: what Tinker does, then what to
  send, grouped by what a person does (build something, their projects, ask anything, while it
  works), each with one line on what it does, and `help`. It is never taken as a chat or a command.
- **CNV-26** `memory --graph` in a conversation makes the page (`tinker memory --graph`) and sends it
  with `send_file`, what tinker printed as its caption. The terminal opens the file in the browser and
  says where it is; a channel that cannot send files sends the caption with the file's path.
- **CNV-27** A glance (a quick command, `help`, `history`, `exercise status`) is answered with `glance`: the
  person's message goes at once, the answer in `GLANCE_KEEP`. `history [PAGE | #N]` lists the things
  shown, newest first, with what was asked for each, or shows thing N again. `clear chat` takes every
  message out of the chats but the status, said in a note that goes in `NOTE_KEEP`. `clear history`, once
  confirmed, forgets the history and clears the chat the same way, and leaves nothing in the history, not
  even what it said.
- **CNV-28** A command that needs words, sent alone (as a chat app's menu sends it: `/fix`), asks for them
  (`ask_for`: what it does, what to send, an example) and takes the next message as them (`fix MESSAGE`),
  even one that starts with a command word. A command of its own instead (sent with its `/`, alone, or the
  same command, as a file sent to read is) drops the question. A command whose words are optional
  (`status`) runs at once.

## channels

- **CHN-1** `Channels` keeps each channel's settings in the home's `channels`, and its secrets apart
  in `channel_secrets`. The database is readable by its owner only, even one made before that was
  so. `make(name)` builds the channel of its kind, and an unknown name or kind ends
  with `SystemExit`. `enabled()` gives nothing when `TINKER_CHANNELS=off`.
- **CHN-2** `ChannelCommand.run` lists by default and rejects an unknown action. `list` shows each
  channel and its chats. `add` (from options or questions) checks the name, refuses a name taken,
  checks the token, and saves the channel disabled. `remove` drops the settings and the secrets.
  `enable` and `disable` switch a channel.
- **CHN-3** `TelegramChannel.call` POSTs form fields (`None` left out) to `/bot<token>/<method>`. It
  gives the result of an ok answer, and a refusal is a `ChannelError`. A dropped connection is
  tried once more, and then it is an `OSError`. Too many requests (429) is waited out for the
  `retry_after` seconds Telegram gives, up to 3 times, and then it is a `ChannelError`.
- **CHN-4** `receive` gives the text messages (and a file sent, as **CHN-11** says) and moves the offset past
  every update. A network
  failure gives no messages. `offer` lists every chat command in the bot's menu (`setMyCommands`, so
  typing `/` autofills them), `-` as `_`, each description cut to 256 characters; a failure is ignored.
- **CHN-5** `send` and `edit` post Telegram HTML, and fall back to plain text when the HTML is
  refused. An edit of unchanged text is harmless. A refused edit takes the message out and sends a new one
  in its place. `latest` tracks the newest message still in the chat. `ask_for(chat, question, hint)` sends
  the question with the reply box open (`force_reply`), the hint in it cut to 64 characters.
- **CHN-6** `ask` gives the default when there is no terminal, or ends with `SystemExit` when there
  is none. `ChannelCommand.check` ends with `SystemExit` on a refusal, and tries a dropped
  network again up to `CHECK_TRIES` times.
- **CHN-7** `pair` serves the chat whose sender says yes at the terminal. `start` needs an enabled
  channel (or the one named), and checks each one before listening.
- **CHN-8** `send_file(chat, path, caption)` posts `sendDocument` as `multipart/form-data`: the chat, the
  file under its name, and the caption as Telegram HTML (at most 1024 chars). A failure sends the
  caption alone.
- **CHN-9** Tinker's status is the chat's one pin. Tinker's first pin in a chat takes out every pin from before,
  one at a time (`unpinChatMessage`, never `unpinAllChatMessages`, which Telegram makes wait), and leaves the
  status as it is when it is pinned already; a later status is pinned, the one before going with its message.
  Telegram's note about the pin is taken out after `PIN_NOTE_WAIT`, or at once when Tinker ends.
- **CHN-10** Every message of a chat served (Tinker's, and the person's) is kept in the ledger, going in a
  day; an edit moves that on. `show` keeps the thing it shows, numbered, with the person's words it
  answers (their message within `ASKED_WAIT`). `glance` takes the person's message out at once, and the
  glance before it, and the glance goes in `GLANCE_KEEP`. `collect` takes out the messages whose time is
  up, and `clear` every message but the status. A message taken out without a network stays in the
  ledger; one Telegram refuses to take out is forgotten.
- **CHN-11** A file sent in an allowed chat is downloaded into the home (`Home.reading_file`) and taken as `read PATH`:
  the link Telegram gives a bot holds its token, so it is never passed on. A file over `BOT_DOWNLOAD_BYTES`, or one
  that cannot be downloaded, is said to the person, and nothing is read. A file from any other chat is ignored.

## command line (tinker.py)

- **CLI-1** `Cli.parse`. Every command is a word (`tinker task …`), and the old `--task` form is an
  error. An unknown command is an error. `confirm` is `resume`. A project is named only as `@ID` or
  `@folder`, before the command's words. `--workdir` defaults to `.`, except for `task`. A task's
  words are joined, and a last word that is an existing `.md`/`.txt` file is its spec. `rename`
  needs a valid folder name. `learn` takes one or two words. An option a command does not take is an
  error.
- **CLI-2** `tinker help` lists every command by group, the options, and examples. With
  `TINKER_FORMAT=markup`, it lists what a chat can send. `tinker help COMMAND` shows that command's
  arguments, and an unknown command is an error.
- **CLI-3** `Commands`. `use-model` accepts only a measured model. `use-web` keeps the backend.
  `runs` lists the runs, newest first. `status` shows the criteria and the open phase. `rename`
  renames the folder of a project whose plan waits, and its pointer. `config` shows the settings,
  and `config KEY VALUE` sets one.
- **CLI-4** `Doctor` checks Ollama, the model, the embedding model, the web and the command. It
  exits 1 only when a required check fails.
- **CLI-5** `ProjectFolder.make(task)` makes a new folder in the projects folder. Its name comes from
  the model (when it is valid) or from the task's words, and it gets `-2`, `-3` … when the name is
  taken.
- **CLI-6** `tinker log [@PROJECT]` shows what the project's last runs said. `--calls` shows its
  model calls and details instead.
- **CLI-7** `CycleRunner.run` runs the phases from the saved one, in order, saving after each, and
  formats after verify. A run with `--web` that is past recall recalls first. It gives `blocked` when
  a phase stops, and then disproves the knowledge used in that step. It gives `awaiting` after the
  phase its kind confirms after (`CONFIRM_AFTER`), unless the cycle builds without confirmation, and
  `done` otherwise. Every phase has a phase class.
- **CLI-8** `main()` ends a run cleanly: Ctrl-C ends with `Interrupted.`, and Ollama and file errors
  end with their message, never a traceback.
- **CLI-9** Built end to end: `tinker task` with a model that answers like a real one
  builds a project in its folder. Each spec point gets a test, the code passes them (a wrong first
  version is fixed by the verify loop), the README's examples run, the final review passes, lessons
  are kept, SPEC.md is written, the run is pointed to from the home and the exit code is 0.
- **CLI-10** `tinker learn NAME "DEFINITION"` (or an alias in the person's shell) keeps the command,
  with no model. `tinker learn WHAT` needs a measured model, an embedding model and the web, all
  checked before anything is loaded. Learning nothing ends with an error. However it ends, it prints
  the time it took (`Time taken: 8m 34s`). It keeps a `Tracker` as a run does: its messages, what
  the model is doing (in plain words), its steps and its end (learned, with the summary, or why it
  stopped), so a chat that started it shows them; the tracker leaves the home at the end unless that
  conversation reads it last.
- **CLI-11** `tinker memory` shows one part per language (built, or read from the web): how many projects built
  it and when its docs were read, then the toolchain that worked (one command a row), its proven facts, fixes and
  lessons (each marked proven or not yet). Then the lessons for any language, the other topics learned, the
  person's commands, how many items are retired, and what to do next. A part with nothing in it is left out;
  with nothing learned at all, it says how to start. `tinker models` lists Ollama's models
  with what was measured, and marks the one in use.
- **CLI-12** `tinker task` builds straight through, with no confirmation. Given `--confirm`, it waits
  once its plan is made: it prints the plan to confirm and exits 0 as `awaiting`. `tinker confirm` builds it, and `tinker amend CHANGE` changes the plan and waits again.
  A run that stops (`blocked`) says why and how to go on.
- **CLI-13** A run works on one cycle. For `add` or `fix`, that is a new feature or fix cycle,
  recorded in the run's `features`. Otherwise it is the unfinished cycle, or else the project's own
  (`base`). A new cycle waits for confirmation only when the run was given `--confirm`, and keeps that
  for the runs that go on with it.
