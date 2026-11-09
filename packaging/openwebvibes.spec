Name:           openwebvibes
Version:        1.1.6
Release:        1.rc1
Summary:        Open Web Vibes: the Open WebUI fork with the context gate
License:        MIT
Source0:        openwebvibes.tar.gz
BuildArch:      noarch

Requires:       bash
Requires:       python3
Requires:       tmux
Requires:       curl

%description
Replaces stock Open WebUI with the fork. One product, one port, one data dir.

The packaged build carries the request-shaping gate (ex_wiring.prompt_gate): the prompt is measured
after tool results are materialized, stale tool output is trimmed, and when the honest count crosses
the rollover point the oldest sections are evicted. The verbatim dump is written before anything
leaves the prompt, the evicted span is handed to the vector store, and what comes back into the
prompt is an addressable index plus the last reasoning turn. The output cap is fitted to the room the
prompt leaves, so a request that would be refused by the engine is refused locally with a readable
reason instead. The window is the smaller of the configured value and the engine handshake, so a
128k machine cannot plan for 262144.

%prep
%setup -q -n openwebvibes

%install
rm -rf "%{buildroot}"
install -d -m 755 "%{buildroot}/usr/lib/openwebvibes"
install -d -m 755 "%{buildroot}/usr/lib/openwebvibes/launcher"
install -d -m 755 "%{buildroot}/usr/bin"
install -d -m 755 "%{buildroot}/etc/openwebvibes"
install -d -m 755 "%{buildroot}/etc/s6-overlay/s6-rc.d/openwebvibes"
install -d -m 755 "%{buildroot}/etc/profile.d"
install -d -m 755 "%{buildroot}/usr/lib/systemd/system"

cp -a backend "%{buildroot}/usr/lib/openwebvibes/"
cp -a build "%{buildroot}/usr/lib/openwebvibes/"
# env.py reads CHANGELOG.md at import time. Shipping it is the primary fix; the code now also tolerates
# its absence so a partial install cannot stop the webui from booting.
if [ -f CHANGELOG.md ]; then
    install -m 644 CHANGELOG.md "%{buildroot}/usr/lib/openwebvibes/CHANGELOG.md"
fi

install -m 755 packaging/launcher/owv.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv.sh"
install -m 755 packaging/launcher/owv-packaged.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv-packaged.sh"
install -m 755 packaging/launcher/owv-kill.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv-kill.sh"
install -m 755 packaging/launcher/owv-aliases.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv-aliases.sh"
install -m 755 packaging/launcher/owv-status.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv-status.sh"
install -m 755 packaging/install-detect.sh "%{buildroot}/usr/lib/openwebvibes/install-detect.sh"
install -m 755 packaging/ensure-open-terminal.sh "%{buildroot}/usr/lib/openwebvibes/ensure-open-terminal.sh"
ln -s ../lib/openwebvibes/launcher/owv-packaged.sh "%{buildroot}/usr/bin/owv"

install -m 644 packaging/etc/openwebvibes/owv.conf "%{buildroot}/etc/openwebvibes/owv.conf"
install -m 644 packaging/profile.d/openwebvibes.sh "%{buildroot}/etc/profile.d/openwebvibes.sh"
install -m 644 packaging/s6/openwebvibes/type "%{buildroot}/etc/s6-overlay/s6-rc.d/openwebvibes/type"
install -m 755 packaging/s6/openwebvibes/run "%{buildroot}/etc/s6-overlay/s6-rc.d/openwebvibes/run"
install -m 644 packaging/systemd/openwebvibes.service "%{buildroot}/usr/lib/systemd/system/openwebvibes.service"

%post
install -d -m 755 /var/lib/openwebvibes 2>/dev/null || true
# Install-time detection of existing stack versus a fresh machine. This is the same surface as the
# Setup tab; it only decides whether the setup workflow auto-runs.
/usr/lib/openwebvibes/install-detect.sh > /var/lib/openwebvibes/install-report.json 2>/dev/null || true

# Open Terminal is not a package dependency, so a fresh machine gets the fork code with nothing to
# start: empty uv cache, no importable module, health window expires, launcher continues without it.
# Detect against the interpreter that will run it and install once if absent. Non-fatal, because an
# offline install must not fail the whole package.
/usr/lib/openwebvibes/ensure-open-terminal.sh > /var/lib/openwebvibes/open-terminal-report.json 2>/dev/null || true

%files
/usr/lib/openwebvibes
/usr/bin/owv
/etc/openwebvibes/owv.conf
/etc/profile.d/openwebvibes.sh
/etc/s6-overlay/s6-rc.d/openwebvibes
/usr/lib/systemd/system/openwebvibes.service

%changelog
* Mon Nov 09 2026 Qwen 3.8 <qwen@localhost> - 1.1.6-1.rc1
- Scaled fit: the gate numbers are shares of the window the engine enforces, not constants written
  for a 256k model. send_limit is window minus answer room minus a 1000 token buffer that scales with
  the window; the 70% setting is the shed trigger, not the ceiling. Verified at 262144, 131072, 32768.
- Tool loop: the gate re-measures at every iteration, on the list as it will actually go out, and
  refuses instead of sending a request it cannot fit. One tightened retry, capped by maxIterations.
- Agentic flush: the message list is rebuilt per iteration; preamble, journal index, current user
  turn, current tool exchange and prefill survive, everything else is evicted, and each tool result is
  capped with a pointer to the full text on disk.
- Engine calibration: the gate keeps the engine's own count against its own measurement for the same
  request. The refused request measured 224174 by the gate and 276348 by the engine, which is a ratio
  and not noise.
- shed_boundary now clamps to the message being answered instead of allowing a boundary past it.
- Fitting no longer removes history with no journal copy when the rollover is off.
- Engine phase stats: prefill, thinking and answering reported separately, one JSON line per request.

* Mon Nov 09 2026 Qwen 3.8 <qwen@localhost> - 1.1.5-1.rc1
- Context gate: the count now includes the text inside message output items. A count that looked
  only at content reported a chat as 17,885 tokens while the engine counted 398,702 for the same
  request, so the gate said the prompt fit and the engine refused it with nothing trimmed.
- Journal: the entry lookup matches the header line, which is what dedupe depends on. Anchored
  against a whole section it never fired, so entries were re-appended every turn (127 sections
  over 52 distinct ids on one chat). Reads now have a budget, and the truncation is reported in
  the text rather than left silent.
- Tool window: bounded by size as well as by count. Outputs inside the window were kept verbatim
  at any size, which is how a single output reached 928 KB. An in-window output is now journalled
  so the pointer names text that exists, and the window carries a byte total.
- Loop guard: an undeclared name that resolves to exactly one declared tool is reported as a
  correction instead of shipped as text. One candidate is a correction, two or more stays a guess.
  A fit of zero is treated as a refusal, since a prompt that leaves no room for an answer cannot
  produce one.
* Mon Nov 09 2026 Qwen 3.8 <qwen@localhost> - 1.1.4-1.rc1
- Loop guard: the shape detector reports every shape a tool call can arrive in, not only the template
  form. A call written as name(arg) on its own line, or an <invoke> block inside a code fence, was read
  as a formatting miss because the name was declared, so the turn answered with the markup itself and
  nothing executed. Both shapes now force one bounded re-request, still capped by shapeRetries and still
  refused after items have streamed.
- Launcher: DATA_DIR defaults to the fork's own data dir and is exported. It previously defaulted to the
  stock instance's directory, so a machine that had just run sync-owv read a different database than the
  one the alias wrote: history was intact, the model list was not.
- Launcher: profiles are read from ~/Documents/llama_profiles through a fixed key-to-flag table, so ngl,
  cache type, batch, threads, flash attention and extra_args apply instead of being dropped, and a
  profile's own llama_bin is honoured when no explicit override is set. Paths resolve from the runtime
  user's home rather than a literal /home/<name>.
- Launcher: --flash-attn is emitted with a value. The ternary build takes on|off|auto, so a bare boolean
  flag consumed the next token and the server refused to start. The startup params report also no longer
  indexes a fixed dict with a hyphenated flag name, which raised KeyError on the first extra_args value.
- kill-owv: the port-holdout sweep goes through the same ownership and scope checks as the rest of the
  script; a port alone is not proof of scope. Scope now accepts the runtime dir as evidence, which is the
  only evidence available for an s6 or systemd started service, and an in-scope process owned by root is
  reported as needing --sudo instead of being silently skipped. Zombies are reported as unkillable rather
  than attempted.
- Aliases: the packaged kill is also available as owv-kill, because a user's shell rc can already define
  kill-owv and profile.d cannot be relied on to win that name.

* Mon Nov 09 2026 Qwen 3.8 <qwen@localhost> - 1.1.3-1.rc1
- New packaging/ensure-open-terminal.sh: detects open_terminal by import against the interpreter the
  launcher will use, then installs it once (uv first because the fleet venvs carry no pip, pip with the
  PEP 668 flag as fallback). Called from %post and from the launcher's terminal stage.
- Launcher terminal resolution order is now cache, import, install, uvx. Previously a machine with an
  empty cache fell to `uvx --offline`, which resolves to nothing, so the terminal never started and the
  health timeout read as a dead port. A venv cannot see ~/.local site-packages, which is why the check
  is an import and not a file lookup.
- The health failure now names the port and the log file instead of warning generically.
- Launcher: the stale-build check no longer runs a find against a src directory the packaged install
  never contains. Under pipefail the pipeline status was find's, so the assignment failed, errexit
  fired right after the terminal stage, and the cleanup trap tore down the engine and a terminal that
  had just become healthy. Only affects RPM installs, since a git checkout has src.
- Terminal resolution probes the full import set, not just the package. open_terminal imports nbformat
  at module level, and on both fleet boxes the package resolved while nbformat was absent: banner,
  traceback, expired health window.
- CHANGELOG.md is now in the package archive. env.py read it at import time and the except branch
  called pkgutil.get_data, which raises FileNotFoundError when the file is missing, so the module import
  died on every packaged install. The fallback is now guarded.

* Sun Nov 08 2026 Qwen 3.8 <qwen@localhost> - 1.1.2-1.rc1
- Journal tool registered in the builtins manifest with index, read and reconstruct, so the pointer
  the prompt prints now resolves to a callable tool.
- New module ex_reconstruct.py: the minimum set to restart a conversation in a fresh window (prior
  context, the last full reasoning loop, the last response, the last user message) written as plain
  text every turn.
- The handoff and the loop-guard note arrive as system instructions, not as user turns, and the block
  states plainly that this is a new conversation and not something the user said.
- evict_to_fit can now evict user turns; a chat grown by user text no longer has nothing left to
  shed and no longer ends in a refusal. The current request is protected by identity, not by index.
- Safety net: when nothing fits, the gate builds the minimum prompt that still contains a real
  request instead of stopping at a refusal.
- Stats row: the context chip is removed. A permanent marker on a system that evicts every turn is
  not a signal, and the number under it counted requests.
- Token rate chips now obey the source setting; the duplicate selector in the composer menu is gone.
- Loop guard: a tool call cut off mid-run reports as truncated instead of as an empty call, and the
  note no longer orders another call.
- leanrea tool: the budget line is accounting only and defaults off behind CONTEXT_BUDGET_NOTES. It
  was the reason agents shortened their own reasoning to a limit they had only been hinted at.
* Sun Nov 08 2026 Qwen 3.8 <qwen@localhost> - 1.1.0-1.rc1
- Stats row: the ctx chip reports the window fill (request size, or last request size plus this
  turn), not a per-turn count. The chip names its source.
- Engine log: prefill and generation numbers are kept apart, so the fraction grows in the right
  direction across a chat.
- Dev instance reads its own engine log through OWV_ENGINE_LOG instead of the shared state dir.
- Settings panel: the panel is the scroll container, and the Help tab has its nav button.
- Packaging: Source0 added (rpmbuild could not find the source), the source archive no longer
  contains itself, and *.spec no longer hides the spec from git.

* Sat Nov 07 2026 Qwen 3.8 <qwen@localhost> - 1.0.0-1.rc1
- Context gate: measure after tool results are materialized, trim stale tool output, force-evict at
  the rollover point, fit the output cap to the room left, refuse locally before the engine does.
- Rollover shed: verbatim dump written first, evicted span handed to the vector store, prompt keeps
  the index plus the last reasoning turn.
- Stats row: context rollover chip, dimmed before the first shed, lit after, shed count per chat.
- Packaging: first RPM, launcher installed under /usr/lib/openwebvibes, config search prefers
  /etc/openwebvibes/owv.conf then the user file, s6 service and systemd unit.
