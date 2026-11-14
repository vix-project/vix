# Declarative runit services

Vix can build runit service directories from Nix modules using the nixpkgs
module system (`lib.evalModules`). This works on a Nix host, including NixOS;
it does not require a running Vix installation or NixOS service modules.

This is the build step only. It does not activate services, manage accounts,
install configuration files, or change `/etc/sv` or `/var/service`.
The existing services in `over/etc/sv` are still used by the ISO.

## Build

From the repository root:

```sh
nix build --file . services --arg modules '[ ./examples/runit.nix ]'
```

This uses `<nixpkgs>` from the host's Nix search path. To choose a particular
nixpkgs checkout, add `-I nixpkgs=/path/to/nixpkgs`.

The example produces:

```text
result/
└── hello/
    ├── run
    └── log/
        └── run
```

`result` is a link to immutable templates in the Nix store. Keep it (or
another GC root) while using the templates so their package dependencies
are not garbage collected. Building does not start the example HTTP server.

## Configuration

```nix
{ pkgs, ... }: {
  services.runit.hello = {
    enable = true;
    environment.PYTHONUNBUFFERED = "1";
    run = ''
      exec ${pkgs.python3}/bin/python -m http.server 8080 \
        --bind 127.0.0.1 --directory /srv/public
    '';
    log.enable = true;
  };
}
```

Use store paths such as `${pkgs.python3}/bin/python` to retain dependencies
and avoid relying on a particular system `PATH`. Run daemons in the foreground
with `exec` so runit can supervise and signal them directly. Script bodies use
the nixpkgs runtime shell; supply the body without a shebang.

For each `services.runit.<name>`:

| Option | Default | Meaning |
| --- | --- | --- |
| `enable` | `false` | Include this service in the output. |
| `run` | Required when enabled | Foreground service script body. |
| `finish` | `null` | Optional script run after the service exits. Receives runit's exit code and signal as `$1` and `$2`. |
| `check` | `null` | Optional readiness script used by `sv`; exit zero when ready. |
| `environment` | `{}` | String values exported for `run`, `finish` and `check`. Shell metacharacters are escaped. |
| `log.enable` | `false` | Generate `log/run` and redirect the service's stderr to stdout. |
| `log.run` | `svlogd -tt /var/log/<name>` | Logger script body; the default also creates the log directory. |

Environment keys must be shell variable names. The logger inherits its
supervisor's environment, without the service's `environment` overrides.
Service names must start with a letter, digit or underscore and contain only
letters, digits, underscores, dots and hyphens.

Disabled services are omitted entirely and do not require `run`. Disabling a
service here does not stop any running service: activation is a separate step.
Scripts run as the supervisor's user, from the service directory (or `log` for
the logger). This module does not create users, drop privileges, or order
dependencies. Prepare `/srv/public` and any other runtime files before running
the example; the default logger needs permission to write to `/var/log/<name>`.

Multiple configuration modules can use `imports`, `lib.mkDefault`,
`lib.mkForce`, `lib.mkBefore` and the usual module merging rules:

```sh
nix build --file . services --arg modules '[ ./base.nix ./local.nix ]'
```

The entry point also accepts `pkgs` and exposes `config` and `options` for
programmatic use:

```nix
let
  vix = import ./default.nix {
    inherit pkgs;
    modules = [ ./examples/runit.nix ];
  };
in vix.services # also available as vix.config.system.build.services
```

## Writable runtime directories

Runit creates `supervise` state beside service scripts, including inside `log`.
Do not point `runsv` or `runsvdir` directly at the Nix store templates, or use a
service symlink whose target is an immutable store directory. A future
activation step must create writable service directories and keep their package
closure rooted. The tests demonstrate this with copies in a temporary directory.

## Tests

```sh
nix build --file tests --no-link -L
```

This checks module merging, validation, optional scripts and disabled services,
then uses real `runsv` and `sv` to exercise startup, readiness, logging and
shutdown with a `finish` script. All processes and mutable files live in the
Nix build environment; no host services or system configuration are changed.
