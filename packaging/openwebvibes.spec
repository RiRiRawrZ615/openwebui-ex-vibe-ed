Name:           openwebvibes
Version:        1.1.0
Release:        1.rc1
Summary:        Open Web Vibes: the Open WebUI fork with the context gate
License:        MIT
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
install -d -m 755 "%{buildroot}/usr/lib/systemd/system"

cp -a backend "%{buildroot}/usr/lib/openwebvibes/"
cp -a build "%{buildroot}/usr/lib/openwebvibes/"

install -m 755 packaging/launcher/owv.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv.sh"
install -m 755 packaging/launcher/owv-packaged.sh "%{buildroot}/usr/lib/openwebvibes/launcher/owv-packaged.sh"
install -m 755 packaging/install-detect.sh "%{buildroot}/usr/lib/openwebvibes/install-detect.sh"
ln -s /usr/lib/openwebvibes/launcher/owv-packaged.sh "%{buildroot}/usr/bin/owv"

install -m 644 packaging/etc/openwebvibes/owv.conf "%{buildroot}/etc/openwebvibes/owv.conf"
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
/etc/s6-overlay/s6-rc.d/openwebvibes
/usr/lib/systemd/system/openwebvibes.service

%changelog
* Fri Nov 07 2026 Qwen 3.8 <qwen@localhost> - 1.0.0-1.rc1
- Context gate: measure after tool results are materialized, trim stale tool output, force-evict at
  the rollover point, fit the output cap to the room left, refuse locally before the engine does.
- Rollover shed: verbatim dump written first, evicted span handed to the vector store, prompt keeps
  the index plus the last reasoning turn.
- Stats row: context rollover chip, dimmed before the first shed, lit after, shed count per chat.
- Packaging: first RPM, launcher installed under /usr/lib/openwebvibes, config search prefers
  /etc/openwebvibes/owv.conf then the user file, s6 service and systemd unit.
