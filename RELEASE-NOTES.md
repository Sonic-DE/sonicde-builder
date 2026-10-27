<!--
  - SPDX-FileCopyrightText: None
  - SPDX-License-Identifier: CC0-1.0
-->

# SonicDE Release Notes for Packagers

What changes for packagers in each SonicDE release, newest first. Each release says
which packages were added, renamed or removed, which dependencies changed, and what
you need to do. What users get from each package is in that package's own
`RELEASE-NOTES.md`.

SonicDE ships three release groups together:

- **Core:** the desktop, e.g. 6.8.0.
- **Frameworks:** e.g. 6.31.0.
- **System:** the applications, e.g. 26.08.2.

A few packages keep their own version numbers. Point releases such as 6.8.0.2 fix
single packages between releases.

| SonicDE release | Frameworks | System | Released |
|---|---|---|---|
| [Upcoming](#upcoming-release) | | | not yet released |
| [6.8.0](#680-october-2026) | 6.31.0 | 26.08.2 | October 2026 |
| [6.7.5](#675-september-2026) | 6.30.0 | 26.08.0 | September 2026 |
| [6.7.4](#674-august-2026) | 6.29.0 | 26.04.3.x | August 2026 |
| [6.7.3](#673-july-2026) | 6.28.0 | 26.04.3 | July 2026 |
| [6.7.0 to 6.7.2](#670-to-672-june-2026) | 6.27.0 | | June 2026 |
| [6.6.5](#665-may-2026) | 6.26.0 | | May 2026 |
| [6.6.4](#664-april-2026) | 6.25.0 | | April 2026 |
| [6.6.3](#663-march-2026) | 6.24.0 | | March 2026 |
| [6.6.1 and 6.6.2](#661-and-662-february-and-march-2026) | | | February and March 2026 |
| [6.5.x](#65x-november-2025-to-january-2026) | 6.23.0 | | November 2025 to January 2026 |
| [6.4.5](#645-september-2025) | | | September 2025 |

A package counts as added in the release where it first shipped as a SonicDE
package. The [package index](#package-index) at the end lists every package with its
first release.

## Upcoming release

### Packages added

Build them in this order, before the packages that use them:

1. **sonic-frameworks-logging** (`SonicLogging`, `Sonic::Logging`): needs Qt 6.11 Core.
2. **sonic-frameworks-init-info** (`SonicInitInfo`, `Sonic::InitInfo`): needs Qt 6.11 Core and sonic-frameworks-logging; Qt 6.11 D-Bus is optional.
3. **sonic-frameworks-ipc** (`SonicIpc`, `Sonic::Ipc`): needs Qt 6.11 Core and Network, and sonic-frameworks-logging.

All three need Extra CMake Modules (sonic-frameworks-cmake-modules) and are licensed
GPL-2.0-or-later.

### Dependency changes

- **sonic-login-manager** now requires sonic-frameworks-init-info, sonic-frameworks-ipc and sonic-frameworks-logging.
- **sonic-desktop-interface** and **sonic-screenlocker** now require sonic-frameworks-ipc and sonic-frameworks-logging.
- **sonic-firewall** now requires sonic-frameworks-init-info, sonic-frameworks-ipc and sonic-frameworks-logging.
- **sonic-theme-sdk** no longer needs Plasma5Support. Its `plasmaengineexplorer` program, man page and
  translations are gone; drop them from your package's file list.

### Actions

- **sonic-plasma5-support-library is no longer released.** It has left the Core release list, so it gets no
  new tags. Keep its 6.8.0 package or replace it with your distribution's KDE `plasma5support`.
- **Restart the display manager after upgrading sonic-login-manager.** The greeter, the settings page and the helper now talk over a new protocol, and old and new versions can't talk to each other.
- **Upgrade sonic-desktop-interface as a whole.** The keyboard layout applet and the keyboard service now use keyboard protocol version 2. The screen locker and the applet switch to the new protocol the next time they start.
- **Set the root log directory at build time** with `-DSONIC_LOGGING_ROOT_DIR=…` in sonic-frameworks-logging (default `/var/log/sonic`). sonic-login-manager's `LOG_FILE` option is gone.
- **Ship or update logrotate rules** to cover:
  - `/var/log/sonic/*.last`;
  - service-managed logs such as `/var/log/sonic/soniclogin.log`;
  - `/var/log/sonic-login-manager.err`.

  The shipped OpenRC and dinit rules use `copytruncate` for logs the service manager opened.
- **dinit packages:** install the new `soniclogin-logdir` scripted service. The service files run `soniclogin --prepare-log-directory` before the service manager opens the log.
- **Reinstall anything installed with `./build-repo … --install` or `./build-all --install`.** Staged builds
  wrote the builder's private staging directory (`state/install-stage/<prefix>/lib`) into installed programs'
  library search path (RUNPATH), so root programs such as `sonic-firewall-service` and `soniclogin` would load
  libraries from a directory the building user can write to. Staged builds now turn off ECM's link-path RPATH
  (`-DKDE_SKIP_RPATH_SETTINGS=ON`); an install prefix other than `/usr` gets that prefix's `lib64` and `lib`
  instead. Check with `readelf -d <program> | grep -i runpath`. Distribution packages built outside the
  builder aren't affected.
- **sonic-firewall installs a root daemon, `sonic-firewall-service`,** in the libexec directory, with service
  files for systemd, OpenRC, runit, s6, dinit and sysvinit. Enable it on every init, systemd included, so the
  firewall settings use it; without it they fall back to the KAuth helper, which still works.
  - It uses the same init switches as sonic-login-manager: by default the build machine's inits are detected;
    `-DMANUAL_INITS=ON` installs none, or exactly those named with `-D<INIT>_FOUND=ON`; `-DSPLIT_BUILD=ON`
    installs all of them, one CPack component each. The OpenRC and sysvinit packages both own
    `/etc/init.d/sonic-firewall-service` and conflict; choosing both without `SPLIT_BUILD` stops the configure
    with an error.
  - It needs the system D-Bus and polkit running. Its runit, s6, dinit and sysvinit files depend on a D-Bus
    service named `dbus`; rename the dependency if your distribution calls it something else (for example
    `dbus-srv`).
- **sonic-firewall also installs a KAuth fallback helper,** its D-Bus system service and policy files, and its
  polkit action `org.sonicde.firewall.servicehelper`, which the daemon checks too. Include all of them in the
  package.
- **sonic-login-manager's sysvinit script was rewritten** as an LSB script that needs `start-stop-daemon`.
  Init detection no longer reports sysvinit when OpenRC is present, and choosing both with
  `-DMANUAL_INITS=ON -DOPENRC_FOUND=ON -DSYSVINIT_FOUND=ON` without `SPLIT_BUILD` now stops the configure,
  because both install `init.d/soniclogin`. The BSD `rc.d` script now follows the same switches
  (`BSD_FOUND`), so `-DMANUAL_INITS=ON` leaves it out on BSD too. On an OpenRC system that also has sysvinit,
  check that `/etc/init.d/soniclogin` is the OpenRC script after upgrading.

### Log locations

- **Root programs** log to `/var/log/sonic/<binary>.log`. The directory is root-owned with mode 0755. An older `soniclogin`-owned `/var/log/sonic` is moved aside to `/var/log/sonic.untrusted-<timestamp>` on first start, not deleted.
- **User programs** log to `~/.local/state/sonic/<binary>.log`.
- **journald** also receives all messages where it runs.
- **Programs run by a service manager** (runit, s6, OpenRC `supervise-daemon`, dinit, systemd), and programs whose output goes to `~/.xsession-errors`, log to that output instead of their own file. sonic-login-manager's daemon logs to:
  - `/var/log/sonic/soniclogin.log` on OpenRC and dinit;
  - `/var/log/sonic/soniclogin/` on runit (`svlogd`) and s6 (`s6-log`);
  - the journal on systemd.
- **sonic-firewall's daemon and helpers** log to the journal on systemd. Elsewhere the daemon writes
  `/var/log/sonic/sonic-firewall-service.log`, and the helpers write `/var/log/sonic/kde_ufw_plugin_helper.log`
  and `/var/log/sonic/kcm_firewall_service_helper.log`; cover them in your logrotate rules.
- **Old log files are deleted once, on first start:**
  - `/var/log/sonic-login-manager.log`;
  - `~/.local/state/sonic/desktop-interface.log`;
  - `~/.local/state/sonic/screenlocker.log`;
  - their `.last` copies;
  - runit's `/var/log/sonic-login-manager/` directory.

  A file still in use as the program's own output is kept. `/var/log/sonic-login-manager.err` is kept and still used.

## 6.8.0 (October 2026)

Frameworks 6.31.0, System 26.08.2. Point releases 6.8.0.1 to 6.8.0.3 followed for
sonic-login-manager and a few other packages.

### Packages added

- **sonic-gamma**
- **sonic-keyboard**
- **sonic-power-management**
- **sonic-print-management:** needs CUPS.
- **sonic-silver-sound-theme**
- **sonic-system-settings**
- **sonic-theme-sdk**

### Packages renamed

Replace the old package with the new one and keep an upgrade path:

- **sonic-frameworks-silver-icons** is now **sonic-silver-icons**.
- **silver-sddm** is now **sonic-silver-sddm**.
- **sonic-pyside-setup** is now **sonic-pyside**.

### Dependency changes

- **sonic-keyboard** is now required by sonic-login-manager, sonic-screenlocker and sonic-workspace.
- **sonic-frameworks-quick-silver-style** is now required by sonic-login-manager (6.8.0.2).
- **sonic-frameworks-color-scheme** is now required by sonic-login-manager and sonic-screenlocker (6.8.0.3).
- **Missing dependencies were added to the manifests** of:
  - sonic-daemon;
  - sonic-frameworks-settings-ui and sonic-frameworks-settings-utils;
  - sonic-frameworks-text-editor and sonic-frameworks-ui-components;
  - sonic-screen.

  If your packages were built from older manifests, check them against the current ones.

### Actions

- Core is now based on upstream Plasma 6.8. Rebuild all Core packages together.

## 6.7.5 (September 2026)

Frameworks 6.30.0, System 26.08.0.

### Packages added

- **sonic-tv-interface**
- **sonic-frameworks-qt-integration** (Frameworks)
- **sonic-accounts-integration** (System)
- **sonic-partition-manager** (System)
- **sonic-system-log** (System)

### Packages removed

- **sonic-plasma5-support-library.** No SonicDE package needs the Plasma 5 or Qt 5 support libraries any
  more, so remove it from your SonicDE packages' dependency lists. KDE applications that still need it can use
  KDE's own `plasma5support` package from your distribution; SonicDE doesn't have to provide it.

### Dependency changes

New system build dependencies:

- **bison, flex and perl.**
- **shared-mime-info**, which provides `update-mime-database`.
- **OpenCV 4.7 or newer, or OpenCV 5.**
- **sonic-dr-robotnik** (the crash reporter) now needs the Python modules `psutil`, `pygdbmi` and `sentry-sdk` at run time.

### Actions

- System applications are now based on upstream 26.08, up from 26.04.

## 6.7.4 (August 2026)

Frameworks 6.29.0. This is the release where nearly all of KDE Frameworks became
SonicDE Frameworks.

### Packages added

- **Core:**
  - sonic-breeze-sddm;
  - sonic-rdp-server.
- **Frameworks 6.29.0:**
  - sonic-daemon;
  - sonic-oxygen-icons;
  - sonic-frameworks-:
    - app-info, archive, autocomplete, barcode, bluetooth, bookmarks;
    - data-models, data-views, dbus, device-integration;
    - feeds, file-metadata, holidays, image-formats, internationalization;
    - keyring, modemmanager, networkmanager, notifications, notification-settings;
    - package, progress-ui, pseudo-terminal, purpose, qml-bridge, quick-charts;
    - root-shell, settings, spell-check, status-notification, svg;
    - text-editor, text-widgets, unit-conversion, widgets-addons, xml-gui, zeroconf.
- **System 26.04.3.1:**
  - sonic-ecco;
  - sonic-terminal.
- **Own version:** sonic-frameworks-quick-ui-addons 1.13.1.

### Actions

- Replace every distribution KF6 package with its SonicDE Frameworks counterpart. Don't mix SonicDE Frameworks with distribution KF6 packages of the same library.

## 6.7.3 (July 2026)

Frameworks 6.28.0, with point release 6.28.0.1 in late July and early August.

### Packages added

- **Core:**
  - sonic-activities-stats, sonic-activity-manager-daemon;
  - sonic-bluetooth-runtime, sonic-browser-integration;
  - sonic-disks, sonic-dr-robotnik, sonic-drawing-tablet;
  - sonic-file-search-ui, sonic-firewall;
  - sonic-frameworks-keyring-pam, sonic-frameworks-quick-silver-style;
  - sonic-gtk-theme-bridge, sonic-launcher-menu-edit;
  - sonic-oxygen, sonic-oxygen-sounds;
  - sonic-pipewire, sonic-plasma5-support-library, sonic-polkit-agent, sonic-qt-theme-bridge;
  - sonic-screenies, sonic-silver-gtk, sonic-ssh-password-prompt;
  - sonic-system-monitor, sonic-system-stats;
  - sonic-terminal-tools, sonic-thunderbolt, sonic-vault;
  - sonic-welcome-center, sonic-write-daemon;
  - xdg-desktop-portal-sonicde.
- **Frameworks 6.28.0 / 6.28.0.1:**
  - sonic-frameworks-:
    - addons-downloader, color-scheme, crash-handler, file-search;
    - icon-themes, idle-tracker, quick-desktop-style;
    - settings-ui, settings-utils, syntax-highlighting;
    - text-codec, ui-components, user-feedback;
    - silver-icons (renamed to sonic-silver-icons in 6.8.0).
  - sonic-frameworks-via 6.27.0.
- **System 26.04.3:** sonic-frameworks-io-extras.
- **Own versions:**
  - sonic-crypto-library 2.3.10;
  - sonic-frameworks-io-fuse 6.0.0;
  - sonic-frameworks-sso 8.61.1;
  - sonic-polkit 0.201.1;
  - sonic-pyside-setup 6.11.1.1 (renamed to sonic-pyside in 6.8.0);
  - sonic-qt-accounts-library 1.17.1;
  - sonic-quick-image-editor 0.6.2.1.1.

## 6.7.0 to 6.7.2 (June 2026)

Frameworks 6.27.0.

### Packages added

- **sonic-breeze** (6.7.2)
- **sonic-frameworks-cmake-modules** (6.27.0.1): SonicDE's Extra CMake Modules. Build it first; every SonicDE package needs it.

## 6.6.5 (May 2026)

Frameworks 6.26.0.

### Packages added

- **Core:**
  - sonic-audio-applet-pulse;
  - sonic-mobile-interface;
  - sonic-network-manager;
  - sonic-night-light.
- **Frameworks:** sonic-frameworks-open-collab.
- **System:** sonic-exiv2-library.

## 6.6.4 (April 2026)

Frameworks 6.25.0.

### Packages added

- **Core:**
  - sonic-activities;
  - sonic-decoration;
  - sonic-system-monitor-library;
  - sonic-workspace-addons;
  - sonic-workspace-wallpapers.
- **Frameworks:**
  - sonic-frameworks-core-addons;
  - sonic-frameworks-doctools;
  - sonic-frameworks-gui-addons;
  - sonic-frameworks-keybind;
  - sonic-frameworks-quick-ui;
  - sonic-frameworks-runner.

## 6.6.3 (March 2026)

Frameworks 6.24.0.

### Packages added

- **Core:**
  - sonic-interface-libraries;
  - sonic-keybind-daemon;
  - sonic-login-manager;
  - sonic-screen;
  - sonic-screenlocker.
- **Frameworks:**
  - sonic-frameworks-auth;
  - sonic-frameworks-io.

## 6.6.1 and 6.6.2 (February and March 2026)

### Packages added

- **sonic-desktop-interface** (6.6.1)
- **sonic-screen-library** (6.6.2)
- **sonic-system-info** (6.6.2)

## 6.5.x (November 2025 to January 2026)

Frameworks 6.23.0.

### Packages added

- **sonic-workspace** (6.5.3)
- **sonic-silver** (6.5): SonicDE's default theme.
- **sonic-frameworks-windowsystem** (6.23.0)
- **silver-sddm** (1.0.0, January 2026): renamed to sonic-silver-sddm in 6.8.0.

### Actions

- SonicDE packages no longer support Qt 5 or KDE Frameworks 5. Build them against Qt 6 only.

## 6.4.5 (September 2025)

The first SonicDE release.

### Packages added

- **sonic-win:** SonicDE's X11 window manager and compositor. It replaces the upstream window manager, and SonicDE doesn't support Wayland sessions.

## Package Index

Every SonicDE package and the release it first shipped in. Packages with no release
yet ship in the upcoming release or aren't built on their own.

| Package | First release | SonicDE release |
|---|---|---|
| sonic-accounts-integration | 26.08.0 | 6.7.5 |
| sonic-activities | 6.6.4 | 6.6.4 |
| sonic-activities-stats | 6.7.3 | 6.7.3 |
| sonic-activity-manager-daemon | 6.7.3 | 6.7.3 |
| sonic-audio-applet-pulse | 6.6.5 | 6.6.5 |
| sonic-bluetooth-runtime | 6.7.3 | 6.7.3 |
| sonic-breeze | 6.7.2 | 6.7.0 to 6.7.2 |
| sonic-breeze-sddm | 6.7.4 | 6.7.4 |
| sonic-browser-integration | 6.7.3 | 6.7.3 |
| sonic-crypto-library | 2.3.10 | 6.7.3 |
| sonic-daemon | 6.29.0 | 6.7.4 |
| sonic-decoration | 6.6.4 | 6.6.4 |
| sonic-desktop-interface | 6.6.1 | 6.6.1 |
| sonic-disks | 6.7.3 | 6.7.3 |
| sonic-dr-robotnik | 6.7.3 | 6.7.3 |
| sonic-drawing-tablet | 6.7.3 | 6.7.3 |
| sonic-ecco | 26.04.3.1 | 6.7.4 |
| sonic-exiv2-library | 26.08.0 | 6.6.5 |
| sonic-file-search-ui | 6.7.3 | 6.7.3 |
| sonic-firewall | 6.7.3 | 6.7.3 |
| sonic-frameworks-addons-downloader | 6.28.0.1 | 6.7.3 |
| sonic-frameworks-app-info | 6.29.0 | 6.7.4 |
| sonic-frameworks-archive | 6.29.0 | 6.7.4 |
| sonic-frameworks-auth | 6.24.0 | 6.6.3 |
| sonic-frameworks-autocomplete | 6.29.0 | 6.7.4 |
| sonic-frameworks-barcode | 6.29.0 | 6.7.4 |
| sonic-frameworks-bluetooth | 6.29.0 | 6.7.4 |
| sonic-frameworks-bookmarks | 6.29.0 | 6.7.4 |
| sonic-frameworks-cmake-modules | 6.27.0.1 | 6.7.0 to 6.7.2 |
| sonic-frameworks-color-scheme | 6.28.0.1 | 6.7.3 |
| sonic-frameworks-core-addons | 6.25.0 | 6.6.4 |
| sonic-frameworks-crash-handler | 6.28.0 | 6.7.3 |
| sonic-frameworks-data-models | 6.29.0 | 6.7.4 |
| sonic-frameworks-data-views | 6.29.0 | 6.7.4 |
| sonic-frameworks-dbus | 6.29.0 | 6.7.4 |
| sonic-frameworks-device-integration | 6.29.0 | 6.7.4 |
| sonic-frameworks-doctools | 6.25.0 | 6.6.4 |
| sonic-frameworks-feeds | 6.29.0 | 6.7.4 |
| sonic-frameworks-file-metadata | 6.29.0 | 6.7.4 |
| sonic-frameworks-file-search | 6.28.0 | 6.7.3 |
| sonic-frameworks-gui-addons | 6.25.0 | 6.6.4 |
| sonic-frameworks-holidays | 6.29.0 | 6.7.4 |
| sonic-frameworks-icon-themes | 6.28.0.1 | 6.7.3 |
| sonic-frameworks-idle-tracker | 6.28.0.1 | 6.7.3 |
| sonic-frameworks-image-formats | 6.29.0 | 6.7.4 |
| sonic-frameworks-init-info | — | Upcoming |
| sonic-frameworks-internationalization | 6.29.0 | 6.7.4 |
| sonic-frameworks-io | 6.24.0 | 6.6.3 |
| sonic-frameworks-io-extras | 26.04.3 | 6.7.3 |
| sonic-frameworks-io-fuse | 6.0.0 | 6.7.3 |
| sonic-frameworks-ipc | — | Upcoming |
| sonic-frameworks-keybind | 6.25.0 | 6.6.4 |
| sonic-frameworks-keyring | 6.29.0 | 6.7.4 |
| sonic-frameworks-keyring-pam | 6.7.3 | 6.7.3 |
| sonic-frameworks-logging | — | Upcoming |
| sonic-frameworks-modemmanager | 6.29.0 | 6.7.4 |
| sonic-frameworks-networkmanager | 6.29.0 | 6.7.4 |
| sonic-frameworks-notification-settings | 6.29.0 | 6.7.4 |
| sonic-frameworks-notifications | 6.29.0 | 6.7.4 |
| sonic-frameworks-open-collab | 6.26.0 | 6.6.5 |
| sonic-frameworks-package | 6.29.0 | 6.7.4 |
| sonic-frameworks-progress-ui | 6.29.0 | 6.7.4 |
| sonic-frameworks-pseudo-terminal | 6.29.0 | 6.7.4 |
| sonic-frameworks-purpose | 6.29.0 | 6.7.4 |
| sonic-frameworks-qml-bridge | 6.29.0 | 6.7.4 |
| sonic-frameworks-qt-integration | 6.30.0 | 6.7.5 |
| sonic-frameworks-quick-charts | 6.29.0 | 6.7.4 |
| sonic-frameworks-quick-desktop-style | 6.28.0 | 6.7.3 |
| sonic-frameworks-quick-silver-style | 6.7.3 | 6.7.3 |
| sonic-frameworks-quick-ui | 6.25.0 | 6.6.4 |
| sonic-frameworks-quick-ui-addons | 1.13.1 | 6.7.4 |
| sonic-frameworks-root-shell | 6.29.0 | 6.7.4 |
| sonic-frameworks-runner | 6.25.0 | 6.6.4 |
| sonic-frameworks-settings | 6.29.0 | 6.7.4 |
| sonic-frameworks-settings-ui | 6.28.0 | 6.7.3 |
| sonic-frameworks-settings-utils | 6.28.0 | 6.7.3 |
| sonic-frameworks-spell-check | 6.29.0 | 6.7.4 |
| sonic-frameworks-sso | 8.61.1 | 6.7.3 |
| sonic-frameworks-status-notification | 6.29.0 | 6.7.4 |
| sonic-frameworks-svg | 6.29.0 | 6.7.4 |
| sonic-frameworks-syntax-highlighting | 6.28.0.1 | 6.7.3 |
| sonic-frameworks-text-codec | 6.28.0 | 6.7.3 |
| sonic-frameworks-text-editor | 6.29.0 | 6.7.4 |
| sonic-frameworks-text-widgets | 6.29.0 | 6.7.4 |
| sonic-frameworks-ui-components | 6.28.0 | 6.7.3 |
| sonic-frameworks-unit-conversion | 6.29.0 | 6.7.4 |
| sonic-frameworks-user-feedback | 6.28.0 | 6.7.3 |
| sonic-frameworks-via | 6.27.0 | 6.7.3 |
| sonic-frameworks-widgets-addons | 6.29.0 | 6.7.4 |
| sonic-frameworks-windowsystem | 6.23.0 | 6.5.x |
| sonic-frameworks-xml-gui | 6.29.0 | 6.7.4 |
| sonic-frameworks-zeroconf | 6.29.0 | 6.7.4 |
| sonic-gamma | 6.8.0 | 6.8.0 |
| sonic-gtk-theme-bridge | 6.7.3 | 6.7.3 |
| sonic-interface-libraries | 6.6.3 | 6.6.3 |
| sonic-keybind-daemon | 6.6.3 | 6.6.3 |
| sonic-keyboard | 6.8.0 | 6.8.0 |
| sonic-l10n-templates | — | translation templates, not built |
| sonic-launcher-menu-edit | 6.7.3 | 6.7.3 |
| sonic-login-manager | 6.6.3 | 6.6.3 |
| sonic-mobile-interface | 6.6.5 | 6.6.5 |
| sonic-network-manager | 6.6.5 | 6.6.5 |
| sonic-night-light | 6.6.5 | 6.6.5 |
| sonic-oxygen | 6.7.3 | 6.7.3 |
| sonic-oxygen-icons | 6.29.0 | 6.7.4 |
| sonic-oxygen-sounds | 6.7.3 | 6.7.3 |
| sonic-partition-manager | 26.08.0 | 6.7.5 |
| sonic-pipewire | 6.7.3 | 6.7.3 |
| sonic-plasma5-support-library | 6.7.3 | 6.7.3 (removed in 6.7.5; last tagged 6.8.0) |
| sonic-polkit | 0.201.1 | 6.7.3 |
| sonic-polkit-agent | 6.7.3 | 6.7.3 |
| sonic-power-management | 6.8.0 | 6.8.0 |
| sonic-print-management | 6.8.0 | 6.8.0 |
| sonic-pulseaudio-library | — | not yet released |
| sonic-pyside | 6.11.1.1 | 6.7.3 (as sonic-pyside-setup) |
| sonic-qt-accounts-library | 1.17.1 | 6.7.3 |
| sonic-qt-theme-bridge | 6.7.3 | 6.7.3 |
| sonic-quick-image-editor | 0.6.2.1.1 | 6.7.3 |
| sonic-rdp-server | 6.7.4 | 6.7.4 |
| sonic-screen | 6.6.3 | 6.6.3 |
| sonic-screen-library | 6.6.2 | 6.6.1 and 6.6.2 |
| sonic-screenies | 6.7.3.1 | 6.7.3 |
| sonic-screenlocker | 6.6.3 | 6.6.3 |
| sonic-silver | 6.5 | 6.5.x |
| sonic-silver-gtk | 6.7.3 | 6.7.3 |
| sonic-silver-icons | 6.28.0 | 6.7.3 (as sonic-frameworks-silver-icons) |
| sonic-silver-sddm | 1.0.0 | 6.5.x (as silver-sddm) |
| sonic-silver-sound-theme | 6.8.0 | 6.8.0 |
| sonic-ssh-password-prompt | 6.7.3 | 6.7.3 |
| sonic-system-info | 6.6.2 | 6.6.1 and 6.6.2 |
| sonic-system-log | 26.08.0 | 6.7.5 |
| sonic-system-monitor | 6.7.3 | 6.7.3 |
| sonic-system-monitor-library | 6.6.4 | 6.6.4 |
| sonic-system-settings | 6.8.0 | 6.8.0 |
| sonic-system-stats | 6.7.3 | 6.7.3 |
| sonic-terminal | 26.04.3.1 | 6.7.4 |
| sonic-terminal-tools | 6.7.3 | 6.7.3 |
| sonic-theme-sdk | 6.8.0 | 6.8.0 |
| sonic-thunderbolt | 6.7.3 | 6.7.3 |
| sonic-tv-interface | 6.7.5 | 6.7.5 |
| sonic-vault | 6.7.3 | 6.7.3 |
| sonic-welcome-center | 6.7.3 | 6.7.3 |
| sonic-win | 6.4.5 | 6.4.5 |
| sonic-workspace | 6.5.3 | 6.5.x |
| sonic-workspace-addons | 6.6.4 | 6.6.4 |
| sonic-workspace-wallpapers | 6.6.4 | 6.6.4 |
| sonic-write-daemon | 6.7.3 | 6.7.3 |
| xdg-desktop-portal-sonicde | 6.7.3.1 | 6.7.3 |
