# Tinker: Specification

Tinker builds what a request asks for with a local model run by Ollama. It plans the work as a small
spec, writes one unit test for each spec point, writes just enough code to pass them, and checks the
result the way a person uses it. It learns from every run. This document says what Tinker is and does.
The tests in `tests/` enforce it.

## How to read this

- Each numbered point (**SET-1**, **WEB-3** ...) states one behaviour. At least one test enforces it,
  marked `@pytest.mark.spec("SET-1")`. `tests/test_spec_coverage.py` checks that every point here has
  a test and that every test names a point that exists.
- A point that the code does not meet yet is listed under [Known deviations](#known-deviations). Its
  test is marked `xfail(strict=True)`: the suite stays green, and the test fails as soon as the code is
  fixed, so the marker is removed and the point is enforced from then on.
- The [design review](#appendix-design-review) at the end is advice, not part of the spec.
- Run the tests from the repository root: `python -m pytest` (with pytest installed).

## The idea

1. Build only what the request clearly asks for. Anything else waits for `tinker add` or `tinker fix`.
2. The plan is a spec of points. Each point has exactly one unit test, written before any code.
3. The code is just enough to pass those tests.
4. The project is done when every spec point's test passes, set up afresh, the way a person runs it.
5. Everything Tinker keeps is plain JSON that a person may edit or delete.

## Architecture

`tinker.py` is the main system: the command line (`Cli`, `Commands`, `Doctor`), one run (`ProjectRun`,
`ProjectFolder`, `Session`) and the cycle of phases (`CycleRunner`). It uses the `tinkerkit` modules.
Each has one responsibility, and each imports only the modules listed above it:

| Module | Responsibility |
|---|---|
| `settings` | every tunable number and fixed name; the Tinker home; small helpers |
| `prompts` | prompt parts (`Part`), replies (`Reply`), every system prompt (`Prompts`) |
| `workspace` | the project's files and the commands run in it (`Workspace`, `Toolchain`, `TestRef`, `Output`, `FileView`, `TaughtCommands`) |
| `model` | the local model: Ollama's API, one request with its budgets and retries (`Model`), measuring a model (`Calibrator`) |
| `project` | the plan and its record (`Project`, `SpecDocument`, `Runs`, `RunChecks`, `Brief`, `Outcome`) |
| `web` | the Internet plugin, the only part that goes on the web (`Internet`, `OllamaWeb`, `DuckDuckGoWeb`, `PageText`) |
| `conversation` | the terminal and the chats as one conversation (`Desk`, `Channel`, `Markup`, `Talk`, `Shell`, `Research`, `Progress`, `Reporter`) |
| `channels` | the chat plugin: Telegram, and `tinker channel` |
| `knowledge` | lessons (`Knowledge`) and learning (`Learner`, `Researcher`, `Library`, `Retriever`, `Embeddings`) |
| `planning` | recall, the spec, the contracts where files meet, and their review |
| `building` | setup, the tests, the code, and fixing until every test passes |
| `finishing` | tidy, the README, the final review and the lessons |

- **ARCH-1** Each `tinkerkit` module imports only the modules listed above it in the table. The one
  exception is `conversation`, which imports the `channels` plugin lazily, inside a function.
- **ARCH-2** The channels plugin is optional. Without `tinkerkit/channels.py`, the conversation has no
  chat channels, and everything else works.
- **ARCH-3** Every model call goes through `Model` and `Ollama`. Every web call goes through
  `Internet`. Every project command goes through `Workspace.run` or `Workspace.start`.

## State on disk

The Tinker home is the `.tinker` folder next to the real `tinker.py` (symlinks resolved). It holds:

| Path | What |
|---|---|
| `config.json` | the settings (`DEFAULT_CONFIG`, overridden by the file) |
| `models/<model>.json` | what calibration measured for a model |
| `languages/<language>.json` | per language: the toolchain that worked, toolchain fixes, lessons |
| `memory.json` | lessons for any language |
| `learned/<topic>.json` | what was learned from the web, with sources |
| `embeddings.json` | the embedding of each learned text |
| `commands.json` | the person's own commands (aliases, programs) |
| `runs/<id>.json` | the run index, so a run ID works from any folder |
| `channels.json`, `channel-secrets.json` | the chat channels and their tokens (mode 600) |
| `running/<pid>.json`, `listening.json`, `channel-<name>.lock` | the conversation's live state |

Each project keeps its own state in `<project>/.tinker/`: `run.json`, `spec.json`, `spec.md`,
`brief.md` and `log.jsonl`. Its plan is also written as `<project>/SPEC.md`, and a copy of everything
a run prints as `<project>/current.log`.

## The cycle

A cycle is the project itself (`base`), a feature (`tinker add`) or a bug fix (`tinker fix`). Its phases
run in order, each saved as it ends, so a run resumes from the phase it reached:

`recall → spec → design → review → setup → tests → code → verify → tidy → docs → final_review → lessons → done`

A bug fix runs `recall → reproduce → verify → final_review → lessons → done`. `--plan-only` stops after
`recall, spec, design, review`. A cycle created with `--confirm` stops after `spec`, awaiting the
person's `confirm` (or `amend`).

---

## settings

- **SET-1** `HOME` is `.tinker` next to the package's real location. `CONFIG_FILE`, `MODELS_DIR`,
  `LANGUAGES_DIR`, `MEMORY_FILE`, `RUNS_DIR`, `COMMANDS_FILE`, `LEARNED_DIR`, `EMBEDDINGS_FILE` and
  `LEARN_LOG` are inside it.
- **SET-2** `DEFAULT_CONFIG` is exactly: model `None`, ollama_url `http://localhost:11434`, keep_alive
  `0`, max_growth `1.15`, build_think `"medium"`, test_timeout `60`, web `"ollama"`, web_fallback `""`,
  web_timeout `30`, learn_missing `2`, embed_model `""`, projects `"~/Projects"`.
- **SET-3** `PHASES`, `FIX_PHASES` and `PLANNING_PHASES` are the phase lists given in [The cycle](#the-cycle).
- **SET-4** `Config` is `DEFAULT_CONFIG` overridden by `config.json`. It is read once and then cached.
  `Config.save(**changes)` writes the merged settings with the changes.
- **SET-5** `Config.check_model(run)` rejects with `SystemExit`: a keep_alive that is neither whole
  seconds nor a duration (`10m`, `1h30m`); a max_growth that is not a number above 1 (booleans
  rejected); a build_think other than `"high"`, `"medium"`, `"low"`, `true`, `false` or `null`. For a
  run, it also rejects a test_timeout that is not a whole number of at least 1.
- **SET-6** `Config.keep_alive(value)` gives whole seconds as an `int` and a duration as it is.
  `Config.ollama_url()` has no trailing slash.
- **SET-7** `JsonFile.write` makes the missing parent folders. It writes indented UTF-8 JSON with
  non-ASCII kept and a final newline. It replaces the file in one step, through a temporary file
  that does not remain.
- **SET-8** `JsonFile.read` gives the default for a missing file. A broken file, or one that holds
  something other than an object, ends with `SystemExit` when it `keeps_work`. Otherwise Tinker
  prints a note and uses the default.
- **SET-9** `Home.ensure()` makes the home folder and writes `config.json` with the defaults on
  first use. A home that cannot be written ends with `SystemExit`.
- **SET-10** `Home.reset(yes)` deletes the home folder. It refuses a folder not named `.tinker`. It
  does nothing when the folder is missing. Without `yes`, and not in a terminal, it deletes nothing
  and exits.
- **SET-11** `Home.parts()` names exactly: languages, memory, learned, models, runs, commands.
  `Home.clear(names, yes)` deletes only the named parts. It rejects an unknown part by listing the
  parts. When those parts do not exist, it says there is nothing to clear. Without `yes`, and not
  in a terminal, it deletes nothing.
- **SET-12** Helpers. `human_duration`: 42.5 gives `42s`, 1762.08 gives `29m 22s`, 4000 gives
  `1h 6m 40s`. `first_line` gives the first line of the text with its ends stripped, cut to the limit with `...`.
  `words` gives the lowercase alphanumeric words. `fingerprint` is the SHA-1 of the text.
  `similarity` is a case-insensitive ratio from 0 to 1.
- **SET-13** `split_text(text, size)` gives chunks of at most `max(size, 200)` chars. It splits
  between paragraphs, cuts a longer paragraph, and loses no text.
- **SET-14** `after_what_they_use(starts, uses, known)` orders the paths so that each comes after
  the paths it uses. It goes depth first from the starts, in their order. A cycle is broken where
  it closes. A path that is not known is left out.
- **SET-15** `groups_fitting(items, room, size)` keeps the items in order, in groups that each fit
  `room` (each item takes `size + 1`). An item larger than the room is a group of its own.
- **SET-16** `Tee` writes to both of its streams and ignores the file's errors. `EventLog.add`
  appends one JSON line with a `time`.
- **SET-17** `Console.step` prints `[HH:MM:SS <duration> (<seconds>s)] message`. `Console.mirror(folder)`
  copies everything printed into `folder/current.log`.
- **SET-18** `Home.keep_awake()` keeps the computer awake with `caffeinate` (macOS) or
  `systemd-inhibit` (Linux), tied to Tinker's process. It does nothing when neither tool is there,
  and it never raises.

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
- **PRM-6** `Reply.files_complete(content)` is true only when the last block has ended, nothing after
  it begins a block, and at least 300 chars follow it (the model is running on). `Reply.wrote(paths)`
  is also true once every wanted path has its block.
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
  (`BRIEF_SYSTEM`, `PICK_FACTS_SYSTEM`, `TOPIC_FOCUS`, `LANGUAGE_FOCUS`, `TOOL_FOCUS`) formats.
- **PRM-15** Each command in the example toolchain is a real command line of its own tools.
  Tinker's own words never appear in it.

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
  `.git`.
- **WS-4** `write` makes the parent folders and ends a non-empty file with a newline. `read` of a
  missing file gives `""`.
- **WS-5** `apply(reply, may_change)` writes each FILE block that `may_change(path, exists)` allows,
  and keeps a backup (`None` for a new file). It refuses with a note: a placeholder path, a path
  outside the project, a file shown only in part, and an empty body where empty is not allowed.
  `revert(change)` restores each backup and deletes the files the change created.
- **WS-6** A LINES block changes only a file shown as numbered lines, and only within the lines
  shown. Of two overlapping blocks, only one is applied, with a note.
- **WS-7** `prepare_env()` makes `.env` from `.env.example` with placeholder values blanked. It
  clears placeholder values in an existing `.env`. `is_placeholder` is true for an empty value,
  `<...>`, `your_...`, `..._here`, `changeme`, `xxx` and the like. It is false for a real value.
- **WS-8** `start(command)` starts a long-running command in the background, with its output in
  `.tinker/app-output.txt`. `stop(process)` ends it and everything it started. An unsafe command
  is not started.
- **WS-9** `TaughtCommands.teach(name, definition)` keeps an alias in `commands.json`. A `source`
  in it becomes `.`. The definition is checked as POSIX shell. The name must be a shell function
  name. `"none"` forgets the command; forgetting one never taught ends with `SystemExit`. An empty
  definition ends with `SystemExit`.
- **WS-10** `TaughtCommands.all()` rejects a `commands.json` whose entries are not aliases or
  programs. `spelled_out(command)` puts each alias's definition in its place, as whole words only.
  `prelude(command)` defines, as shell functions, the aliases the command uses. `Workspace.run`
  puts the prelude first, so a taught alias works in a project's commands.
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

- **MOD-1** `Models.file(name)` is `models/<name>.json`, with characters outside `[A-Za-z0-9._-]`
  replaced by `_`.
- **MOD-2** `Models.selected()` ends with `SystemExit` when no model is in use, when the model in use
  was never measured, or when a measured value (`max_prompt_tokens`, `num_ctx`, `max_reply_tokens`,
  `fix_max_tokens`) is missing, below 1, or a boolean. Otherwise it gives `(name, settings)`.
- **MOD-3** `Models.installed(name)` ends with `SystemExit` when Ollama does not answer, or has no
  model of exactly that name. In that case it suggests close names. Otherwise it gives the name.
- **MOD-4** `Models.context_for(budget, reply, length)` is `budget + reply + 512`, rounded up to a
  multiple of 1024, and at most the model's context length. `Models.reply_limits(reply)` sets the
  longest fix to `min(4096, reply)`.
- **MOD-5** `Models.remember(model)` saves the chars per token the run learned, when it learned one
  that differs from the one saved.
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
  `EARLY_STOP_GRACE` seconds, and the reply is the content as it was at that moment.
- **MOD-10** `Calibrator.shared(context)` sets the prompt budget to `min(8192, max(512, context // 3))`,
  and the longest reply to `max(1024, min(16384, context - budget))`. `safe_ratio()` is
  `CHARS_PER_TOKEN` until measured, and then 97% of the measure, within [2, 4], to 2 decimals.
- **MOD-11** `Calibrator.capacity(target)` gives `(target, None)` when the target reads. Otherwise it
  halves the gap until it is within `CAPACITY_STEP`, and gives `(largest that read, smallest that
  failed)`. Below the smallest usable context it ends with `SystemExit`.
- **MOD-12** `Calibrator.check_fit` ends with `SystemExit` when the weights and `MEMORY_OVERHEAD` are
  more than the GPU memory. With either size unknown, it checks nothing.
- **MOD-13** `Calibrator.run()` measures the model and writes `models/<model>.json` with a prompt
  budget, a context, a longest reply, a longest fix, a think setting and chars per token.
- **MOD-14** `Model` takes its limits from the measured settings. It builds at `build_think` only
  when the model thinks. Its chars per token stay within [2, 4]. `limit_chars(system)` is
  `tokens × PROMPT_HEADROOM × chars_per_token − len(system) − 40`.
- **MOD-15** `Model.ask_once` adds `NO_TOOL_CALLS` to the system prompt. It fits the user message to
  the prompt budget, gives the reply's content, and logs the exchange to the run's log.
- **MOD-16** A reply cut off while it is still thinking is asked again: first with twice the budget,
  up to the cap; then, if `unthinking_retry`, without thinking, with the thought so far to
  conclude. Otherwise the result is `""`, with `overthought` set and `last_thought` kept.
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

## project

- **PRJ-1** `Runs.new_record` gives `{id, task, workdir, spec, created, status: "running"}`, with a
  6-hex-digit ID. `Runs.save` sets `updated` and writes both `.tinker/run.json` and `runs/<id>.json`.
- **PRJ-2** `Runs.find(id)` matches a run by the start of its ID, ignoring case. No match, or more
  than one, ends with `SystemExit`. `Runs.entries()` skips files it cannot read.
- **PRJ-3** `Runs.workdir(id, folder)` gives the run's folder, and its absence ends with
  `SystemExit`. Without an ID, it gives the given folder, resolved, which must exist.
- **PRJ-4** `Runs.open_new` refuses a folder that holds a run, writes the spec text to
  `.tinker/spec.md`, saves a new record and gives the mode `"new"`. `Runs.open_existing` marks the
  run running. It gives the mode `"resume"` with the saved spec text, or `"change"` with the
  feature's spec file.
- **PRJ-5** `Runs.clear_folder` refuses the home folder, the filesystem root, and any folder that
  holds Tinker's home. It keeps the entry that holds the spec file. Without `yes`, and not in a
  terminal, it deletes nothing. Otherwise it empties the folder and forgets the runs registered
  for it.
- **PRJ-6** `RunChecks.before_start`. A new run needs an existing folder that holds no run, and an
  existing spec file when one is given. A saved run needs a valid phase in every cycle. `add`/`fix`
  need a project spec and no unfinished cycle. `amend` needs a spec that awaits confirmation.
  `restart` reads the task and spec text before anything is deleted.
- **PRJ-7** `Project` keeps `criteria` (saved as `steps`), `cycles` and `files` in `spec.json`.
  `open_cycle()` is the first cycle not done. `new_cycle` starts at the first phase of its kind.
  `new_criterion` numbers the criteria `C1, C2 …` (`B…` for a bug) from one counter. `counts()`
  gives `(passing, parked, total)`. `save()` writes `spec.json` and `SPEC.md`.
- **PRJ-8** `criteria_of(path, steps)` gives the criteria whose test file uses the file, or whose text
  names the file (`name.py`, or `stem.` for code). A template's criteria are also those of the
  files that render it.
- **PRJ-9** `entry_files` gives the design's entry point first, then any file the run command names.
  `contracts(paths)` lists each file's contract lines. `register` adds new files without changing
  known ones. `dependencies(read)` gives the dependency files. `uses(path)` gives the files used,
  directly and one level down. `remove_file` deletes a file and drops it from the design.
- **PRJ-10** `SpecDocument` writes SPEC.md: the title, the run and language, a table of criteria
  with readable status, the commands (with aliases spelled out), each cycle (constraints, non-goals,
  interface check, external-service data), the design (files, roles, uses, contracts), and each
  criterion with its source and, unless it passes, its last error.
- **PRJ-11** `Brief.load` gives `""` for a change. It reuses a saved `brief.md`. A spec that fits the
  spec prompt is used whole. A longer spec is condensed chunk by chunk with the model.
- **PRJ-12** `Outcome` starts as `stopped` with exit code 1. `done`, `planned` and `awaiting` exit
  with 0, and `blocked` exits with 1.

## knowledge

- **KNW-1** `Library.slug`: "Test-driven development" gives `test-driven-development`, "C++" gives
  `cpp`, "C#" gives `csharp`, and "Python 3.14" gives `python-3-14` (at most 60 chars). `read(slug)`
  gives `None` for a topic that is missing or unreadable. `topics()` gives every readable topic.
  `language(name)` gives a slug only for a topic learned as a language.
- **KNW-2** `Library.language(name)` finds a language learned without a version (`Python`) for a
  name with one (`Python 3.12`), the same way `Knowledge.language_slug` does.
- **KNW-3** `Library.items(topics)` gives one item per text, as `{topic, name, phase, text}`: the
  summary, the tools, what is installed, research findings, and each fact with its confidence. A
  broken topic is left out, with a note. `shown_in(item, phases)` sends machine items to the
  toolchain and config prompts, tools also to the tests prompt, findings to the spec and design
  prompts, and a fact to its own phase.
- **KNW-4** `Knowledge.language_slug`: "Python 3.12" gives `python`, "JavaScript (Node.js)" gives
  `javascript`, "C++" gives `cpp`, and "C#" gives `csharp`. A language never seen starts empty.
- **KNW-5** `add_lessons(new, confirmed)` confirms known lessons by number. It confirms instead of
  adding a lesson that is like a known one. It skips a lesson with no text or an unknown phase. A
  lesson goes to the language or to any language by its scope. A file keeps at most `MAX_LESSONS`,
  and new lessons always stay. It gives how many were new. `lessons(phases)` gives the most
  confirmed first.
- **KNW-6** Toolchain fixes. `known_fix` replays only a fix for exactly this failure, not tried yet,
  that has helped at least as often as it failed. `remember_fix` keeps the last 20, one per failure.
  `similar_fix` needs a similarity of at least `SIMILAR_FAILURE`. `remember_toolchain` keeps the
  toolchain without its `run` and `check`.
- **KNW-7** `Embeddings` caches each text's vector by its key, for one model. Another model starts
  a new cache. Only missing texts are asked for, in batches of `BATCH`. A wrong reply gives `None`,
  and `failed` says why. `prune` keeps only the texts asked for. `cosine` is the cosine similarity,
  and 0 for a zero vector.
- **KNW-8** `Retriever.by_words` ranks the texts that hold the query's words with BM25, best first.
  `rank` fuses the word and meaning rankings and gives every index: the ones neither finds come
  last. When embeddings fail, the meaning search is turned off for the rest of the run.
- **KNW-9** `Learner.read_plan` needs a name and a search, unless the topic is a language. It keeps
  at most `MAX_QUERIES` searches and `MAX_COMMANDS` commands. `safe_commands` drops a command with a
  shell operator, a command that changes something, and an unsafe command. `read_language_plan`
  keeps one tool per known role and the searches of known sections. A plan that says the request
  is not a language gives `{"not_language": …}`.
- **KNW-10** `Learner.read_facts` keeps facts of a known phase, cut to `FACT_CHARS`, at most
  `FACTS_PER_CHUNK`. `add_facts` keeps a repeated fact once and adds the other page to its `also`.
  `relevant_chunks` gives the page's first chunk and then the best-matching ones, in page order.
  Facts about unreleased versions are dropped.
- **KNW-11** `Learner.learn(topic)` plans, runs the version commands, reads the pages, and saves the
  topic with its facts and sources. When the reading is cut short, or finds nothing, a topic
  learned before is kept. With nothing found and nothing installed, it gives `None`.
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
  cross-checks and analyses the facts, searches the gaps and checks in one more round, and saves the
  topic as research: its questions, findings, contradictions, open gaps, and facts with their sources
  and confidence.
- **KNW-16** A topic the plan says is a programming language is learned as one (`learn_language`):
  its sections and tools are searched, each section keeps its share of facts, and it is saved under
  the language's slug with kind `language`.

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
  it lists with the same stem, or as the only one it lists. It defaults the language to Python.
- **PLN-6** `SpecWriter.take` adds the criteria, puts the files into the design in build order (a
  new project gets a `README.md`), sets the toolchain and the entry, keeps the cycle's non-goals,
  constraints and data, and marks the cycle specified. When a user starts a file but the toolchain
  has no run command, the toolchain is asked for again. A cycle already specified is not planned
  again.
- **PLN-7** `SpecWriter.amend` replaces the cycle's criteria with the amended spec, numbered from
  where they began, records the amendment and what changed (`+ ~ -`), and leaves the spec as it was
  when the model gives nothing usable.
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
  topics' facts in the project, tools and machine facts first. The project's learned language is
  always used.
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
- **BLD-4** A run stopped in the middle of a criterion leaves a restore point. The next run puts the
  files back from it. `failure_key` ignores the test's own name. `roll_back` also drops the files
  created since.
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
  now. A report no test can show is dropped with its test, and the cycle closes as not reproduced.

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
- **FIN-8** `LessonWriter` counts a finished project and keeps its toolchain once. With no problems
  noted, it makes no call. Otherwise it keeps at most 3 new lessons and clears the noted problems.
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
  there is no terminal). `literal` escapes the marks.
- **CNV-2** `Markup.columns(text)` joins the columns of a terminal table with ` | `. It leaves text
  that is not in columns as it is.
- **CNV-3** `pieces(text, size)` splits a message between lines into parts of at most `size` chars,
  and loses no text.
- **CNV-4** `command_of(text, commands)` gives the command a message starts with (`/` allowed). It
  is a command when the word is alone, when the command takes any words, or when it takes one word
  and exactly one follows. Otherwise the message is a chat (`""`).
- **CNV-5** `Channel.said` removes `tinker ` before a command in a conversation. `Channel.show` edits
  the thing's messages, sends parts that no longer fit, deletes the extra ones, adds the update
  time, pins when asked, and sends the message again at the bottom when it is no longer the newest.
- **CNV-6** `Channel.listened` gives only messages from allowed chats. It says a refusal once and
  gives nothing. `claim` lets only one process hold a channel's lock.
- **CNV-7** `ConsoleChannel` prints a dim headline while a thing changes, the whole message when it
  is done, and only the new lines when it grew. `exit` or end of input closes it.
- **CNV-8** `Progress.read(line)` follows a run's printed lines. A step title starts a step, and
  the one before is done. Pass counts, live model output and the latest line are kept. Header
  noise is ignored. The summary after the rule line is collected. `text()`, `brief()`, `ending()`
  and `took()` show them. A summary with `📋 Spec for` is a spec that waits.
- **CNV-9** `Reporter` shows the progress in one message per chat, edited at most every
  `EDIT_EVERY` seconds. It keeps the progress in a shared file, and it ends with the summary,
  unless it is quiet.
- **CNV-10** `LineWatcher` passes writes on unchanged, and gives each whole line to its callback.
  An error in the callback never stops the write.
- **CNV-11** `Talk.model()` is `None` until a measured model is in use. Otherwise it gives the chat
  options (no thinking for a thinking model). `reply` keeps the last `CHAT_KEPT` messages per chat,
  and it says plainly when no model is in use or the model failed.
- **CNV-12** `Shell.propose` gives one command line to confirm, with a warning when it changes or
  deletes things. It gives no command for an empty, multi-line or failed reply. `take` hands the
  proposal over once. `run` runs it in the person's shell and shows the exit code and output.
- **CNV-13** `Research.most_about` keeps the paragraphs that share the most words with the searches,
  in page order, up to `RESEARCH_PAGE_CHARS`. `searches` falls back to the topic itself.
- **CNV-14** `running_elsewhere()` lists the tasks run from other terminals. It skips its own process
  and removes the files of processes that have ended.
- **CNV-15** `Desk.answer` handles a command. `task WHAT` starts a confirmable task, and a bare
  `task` gets its usage. `status` alone gives what runs. `stop` stops the task. A command that
  deletes shows what it deletes and waits for `confirm`. `exit` asks first. A `channel` action
  that needs a terminal says so. Any other command runs as `tinker`.
- **CNV-16** `Desk.settle` runs what waits for a `confirm` (a deletion, a command line). Any other
  message cancels it and says so. `decide` builds or changes the spec that waits; its first
  `confirm` asks for the folder name.
- **CNV-17** `Desk.follow` reads a task's printed lines. It takes the run ID and the folder from the
  header, renames the title after the folder, and keeps a spec that waits.
- **CNV-18** Helpers. `json_in` gives the JSON object in a text, or `{}`. `first_lines` cuts lines
  and counts the rest. `first_words` collapses whitespace and cuts. `text_of` decodes bytes.
  `after_word` drops the command word. `built_project` gives the run built last, or the run named by
  its folder or ID.
- **CNV-19** `emergency MESSAGE` stops the task started here (it saves itself), answers the message
  at once, and then resumes the task. A task run from another terminal is paused by a signal, and
  it goes on in its own terminal. Only one emergency is answered at a time.
- **CNV-20** `run [PROJECT] ARGS [<<< INPUT]` runs a built project the way a person runs it: its run
  command, in its folder and environment, with the arguments quoted and the input given after `<<<`.
  The project is the one named (by folder name or run ID), else the one built last. With no project
  built, or one with no run command, it says so.
- **CNV-21** `Desk.run` claims each chat channel. When another Tinker holds one, it stops if there is
  no terminal, and otherwise leaves that chat out. It shows its status, pinned in each chat, when it
  starts listening, and `not listening` when it ends.
- **CNV-22** `one_shot` says one of the conversation's commands once, in the terminal (`tinker chat`,
  `command`, `research`, `run`, `stop`, `emergency`). Each needs words after it, except `run` and
  `stop`. `command` runs nothing without a yes at the terminal.
- **CNV-23** `research TOPIC` in a chat says when the web or the model is not usable. Otherwise it
  answers from the pages it read and lists them as numbered sources.
- **CNV-24** A task run from a terminal reports to the enabled chats (`Attached`). Its progress is
  kept in `running/<pid>.json`. `STOP` (SIGUSR2) stops it as Ctrl-C does. `PAUSE` (SIGUSR1) stops it
  for an emergency: it marks itself paused and waits until its file is removed (at most
  `RESUME_WAIT` seconds), and then it goes on.

## channels

- **CHN-1** `Channels` reads `channels.json`, and a broken file ends with `SystemExit`. It writes the
  secrets apart, in a file with mode 600. `make(name)` builds the channel of its kind, and an unknown
  name or kind ends with `SystemExit`. `enabled()` gives nothing when `TINKER_CHANNELS=off`.
- **CHN-2** `ChannelCommand.run` lists by default and rejects an unknown action. `list` shows each
  channel and its chats. `add` (from options or questions) checks the name, refuses a name taken,
  checks the token, and saves the channel disabled. `remove` drops the settings and the secrets.
  `enable` and `disable` switch a channel.
- **CHN-3** `TelegramChannel.call` POSTs form fields (`None` left out) to `/bot<token>/<method>`. It
  gives the result of an ok answer, and a refusal is a `ChannelError`. A dropped connection is
  tried once more, and then it is an `OSError`.
- **CHN-4** `receive` gives the text messages and moves the offset past every update. A network
  failure gives no messages.
- **CHN-5** `send` and `edit` post Telegram HTML, and fall back to plain text when the HTML is
  refused. An edit of unchanged text is harmless. A refused edit sends a new message. `latest`
  tracks the newest message still in the chat. `pin` unpins the message pinned before.
- **CHN-6** `ask` gives the default when there is no terminal, or ends with `SystemExit` when there
  is none. `ChannelCommand.check` ends with `SystemExit` on a refusal, and tries a dropped
  network again up to `CHECK_TRIES` times.
- **CHN-7** `pair` serves the chat whose sender says yes at the terminal. `start` needs an enabled
  channel (or the one named), and checks each one before listening.

## command line (tinker.py)

- **CLI-1** `Cli.parse`. Every command is a word (`tinker task …`), and the old `--task` form is an
  error. An unknown command is an error. `confirm` is `resume`. Run IDs must be hex (3 or more
  digits). `--workdir` defaults to `.`, except for `task`. A task's words are joined, and a last word
  that is an existing `.md`/`.txt` file is its spec. `add`, `fix` and `amend` take an optional run
  ID. `rename` needs a valid folder name. `learn` takes one or two words. An option a command does
  not take is an error.
- **CLI-2** `tinker help` lists every command by group, the options, and examples. With
  `TINKER_FORMAT=markup`, it lists what a chat can send. `tinker help COMMAND` shows that command's
  arguments, and an unknown command is an error.
- **CLI-3** `Commands`. `use-model` accepts only a measured model. `use-web` saves the backend.
  `runs` lists the runs, newest first. `status` shows the criteria and the open phase. `rename`
  renames the folder of a spec that waits. `list-config` shows the settings.
- **CLI-4** `Doctor` checks Ollama, the model, the embedding model, the web and the command. It
  exits 1 only when a required check fails.
- **CLI-5** `ProjectFolder.make(task)` makes a new folder in the projects folder. Its name comes from
  the model (when it is valid) or from the task's words, and it gets `-2`, `-3` … when the name is
  taken.
- **CLI-6** `Session.setup` runs setup only when the setup commands or config files changed (or
  when forced), and it prepares `.env`. `run_all_tests` runs the suite once per state of the
  project's files. `note` keeps the last 30 problems. `ask_until_usable` ends with `SystemExit`
  when no reply was usable. `format_code` drops a formatter that fails. `knowledge_parts` gives
  lessons and learned facts as optional parts, plus the person's commands for the toolchain.
- **CLI-7** `CycleRunner.run` runs the phases from the saved one, in order, saving after each, and
  formats after verify. It gives `blocked` when a phase stops, `planned` after planning with
  `--plan-only`, `awaiting` after a spec that needs confirmation, and `done` otherwise. Every phase
  has a phase class.
- **CLI-8** `main()` ends a run cleanly: Ctrl-C ends with `Interrupted.`, and Ollama and file errors
  end with their message, never a traceback.
- **CLI-9** Built end to end: `tinker task` with a model that answers like a real one builds a
  project in its folder. Each spec point gets a test, the code passes them (a wrong first version
  is fixed by the verify loop), the README's examples run, the final review passes, lessons are
  kept, SPEC.md is written, the run is registered and the exit code is 0.
- **CLI-10** `tinker learn NAME "DEFINITION"` (or an alias in the person's shell) keeps the command,
  with no model. `tinker learn WHAT` needs a measured model, an embedding model and the web, all
  checked before anything is loaded. Learning nothing ends with an error.
- **CLI-11** `tinker memory` shows each language's toolchain, fixes and lessons, the lessons for any
  language, the topics learned, and the person's commands. `tinker models` lists Ollama's models
  with what was measured, and marks the one in use.
- **CLI-12** A task run with `--confirm` stops after its spec, prints the spec to confirm, and exits 0
  as `awaiting`. `tinker resume` then builds it. A run that stops (`blocked`) says why and how to
  go on.
- **CLI-13** A run works on one cycle. For `add` or `fix`, that is a new feature or fix cycle, recorded
  in the run's `features`. Otherwise it is the unfinished cycle, or else the project's own (`base`),
  which awaits confirmation when `--confirm` was given.

---

## Known deviations

Each deviation has a strict `xfail` test. It shows the problem today, and it fails once the problem
is fixed, so its marker is removed and the point is enforced from then on.

| Issue | Point | What happens now |
|---|---|---|
| ISSUE-1 | PRM-15 | The prompts' example toolchain says `"format": "ruff check tinker fix --exit-zero . && black ."`. The `--fix` flag became `tinker fix` when the module split renamed commands. Ruff takes `tinker` and `fix` as paths, and `--exit-zero` hides the error, so a model that copies it gets a formatter that silently fixes nothing. |
| ISSUE-2 | CNV-3 | `pieces()` cuts a line longer than the message size to `size` chars. The rest is lost (for example, a long line in a command's output sent to a chat). |
| ISSUE-3 | FIN-3 | The README's examples are checked by substring: `80.33` passes on `80.33000000000004`, and `7` passes on `17`. This is the mistake the test-writing prompt warns the model about. |
| ISSUE-4 | KNW-2 | A language is identified two ways. `Library.slug("Python 3.12")` is `python-3-12`, but `Knowledge.language_slug` gives `python`. A project whose language has a version never uses the language learned. |
| ISSUE-5 | WS-22 | `FileView.window_view` grows a window only downwards. In `for a, b in ((start - 1, end), (start, end + 1))` the tuple is built before the loop body, so the second option sets `start` back and undoes the step up. A fix for a file too large to show whole sees the failing line and what follows, never the function it is in (until the file's end stops the growth). |

---

## Appendix: design review

This is advice, not spec: what makes the design harder to follow or change, and a better way to do it.

1. **Paths are frozen at import, in ten modules.** `HOME`, `RUNS_DIR`, `MODELS_DIR` … are module
   constants that other modules import by value, and `Config` caches its values in a class
   attribute. To move Tinker's home (or test it), about 30 names in ten modules must be patched,
   which is what `tests/conftest.py` has to do. *Better:* one `TinkerHome` object (its root, and a
   property per path), made once in `main()` and handed down, or looked up through one function.
   Hold the config in that object.
2. **Two checks can never run.** `RunChecks.check_planning` and `check_recall_ahead` check
   `tinker resume --plan-only` and `--web`, but `resume` has neither option (`add` and `fix` go
   through `check_open_to_add` instead). *Better:* give `resume` those options, or delete the checks
   and the docstrings that mention them.
3. **Writes that matter most are not atomic.** `run.json`, `spec.json`, `SPEC.md`, `channels.json` and
   `listening.json` are written with `write_text`. A crash in the middle leaves a broken file, and
   `keeps_work` then stops every later command. `JsonFile.write` is atomic, but its temporary name
   (`with_suffix(".tmp")`) is shared: `x.json` and `x.jsonl` both use `x.tmp`, as do two processes
   writing the same file (the Desk and a task can). *Better:* one `atomic_write(path, text)` with a
   unique temporary file in the same folder (`tempfile.NamedTemporaryFile(dir=…, delete=False)` +
   `os.replace`), used for every state file.
4. **`Workspace` needs a resolved root, but nothing says so.** `resolve()` checks
   `root in full.parents` against a resolved path, so a root under a symlink (`/tmp` on macOS) makes
   every path "outside the project". Callers resolve the root today. *Better:* resolve the root in
   `Workspace.__init__`.
5. **`.env` placeholders are guessed too widely.** `PLACEHOLDER_RE` blanks real values such as
   `none`, `null`, `PUT`, `putty`, `todo`, `your-company` and `example_db`, without saying so.
   *Better:* blank only values that are left unchanged from `.env.example`, or that have the obvious
   markers (`<…>`, `…_here`, `changeme`, `xxx`).
6. **A word can be mistaken for a run ID.** Run IDs are 6 hex digits and are matched by prefix. In
   the chat, `run [PROJECT] ARGS` takes a first word of 3 or more characters that starts an ID as
   that project. On the command line, `add`, `fix` and `amend` take a first hex word of 3 or more
   characters that starts an ID as that run. So `run 100 + 5`, `run add 2 3`,
   `tinker fix 100 divided by 0 crashes` or `tinker add bad input handling` can act on another
   project. *Better:* take only a full ID (or an explicit marker such as `@calc`), and match folder
   names exactly.
7. **`Reply.files_complete` does not mean complete.** It is true only when the model ran on for 300+
   chars past its last block. `Model.ask_once` also uses it to judge a cut reply. A reply cut right
   after a complete block therefore counts as unfinished and is asked again. *Better:* name it
   `ran_on_past_answer`, and keep a separate `files_ended(content)` for completeness.
8. **Two lists of commands.** `conversation.LONG/QUICK/DELETES/OWN/ONE_WORD/ANY_WORDS` and
   `Cli.GROUPS/COMMANDS/RUNS` describe the same commands separately, so a new command must be added
   to both, consistently. *Better:* one table of commands (name, arity, kind: long, quick, deletes,
   own) that both the parser and the conversation read.
9. **The chat UI depends on printed wording.** `Progress` drives the chat from regexes over printed
   lines (phase titles, `tests: x/y criteria pass`, `📋 Spec for`, `Done`). `FinalReview.run` branches
   on `"passed once and failed" in problem`. Rewording a message silently breaks the chat, and bug
   criteria (`B3 passes …`) are not counted live. *Better:* emit structured events (the run already
   has `EventLog`) that the reporter reads. Return a reason code, not prose, from `FinalReview.problem`.
10. **`Session` is the real interface of every phase, but it lives in the script.** Every `Phase` and
    `Worker` reaches into `session.model/project/ws/toolchain/knowledge/...`. That interface is
    defined in `tinker.py`, below the modules that depend on it, and is enforced by nothing.
    *Better:* move `Session` (and `CycleRunner`) into `tinkerkit/session.py`, so the layering in
    ARCH-1 includes it, and keep `tinker.py` to the command line.
11. **Small inconsistencies.** The overlapping-LINES note says "kept the first", but it keeps the
    block lower in the file. A file given both as a FILE and a LINES block silently loses its line
    edits. `Project.criterion(id)` raises `StopIteration` when missing, but `Project.cycle(id)` gives
    `None`. `Project.current` is only another name for `criteria`. The phase numbers in the class
    docstrings (`CodeWriter` 6, `Verifier` 6, `Tidy` 9, `ReadmeWriter` 9, `LessonWriter` 12)
    disagree with `PHASES`. A stray comment at `tinker.py:242` is the end of the comment that
    `settings.TOPIC_RELEVANT` lost. `current.log` is written into the project's own folder, among
    its files, not into `.tinker/`. The project's SPEC.md lists its commands without `activate`,
    which the README shows.
