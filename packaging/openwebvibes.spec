Name:           openwebvibes
Version:        1.1.2
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

install -m 755 packaging/launcher/owv.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv.sh"
install -m 755 packaging/launcher/owv-packaged.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv-packaged.sh"
install -m 755 packaging/launcher/owv-kill.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv-kill.sh"
install -m 755 packaging/launcher/owv-aliases.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv-aliases.sh"
install -m 755 packaging/launcher/owv-status.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv-status.sh"
install -m 755 packaging/install-detect.sh "%{buildroot}/usr/lib/openwebvibes/install-detect.sh"
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

%files
/usr/lib/openwebvibes
/usr/bin/owv
/etc/openwebvibes/owv.conf
/etc/profile.d/openwebvibes.sh
/etc/s6-overlay/s6-rc.d/openwebvibes
/usr/lib/systemd/system/openwebvibes.service

%changelog
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
