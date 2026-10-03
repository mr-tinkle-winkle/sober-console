{
  description = "sober-console - an F9-style developer-console log overlay for Sober (Roblox on Linux)";

  inputs = {
    # Overridden by the parent flake's `inputs.nixpkgs.follows = "nixpkgs-unstable"`
    # (same pattern as afterglow) -- this default is only used if sober-console is
    # ever built standalone.
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ];

      # The overlay's layer-shell shim (native/). LayerShellQt (KDE's wlr-layer-shell
      # implementation) has a C++ API only -- no Python bindings exist -- so a small C shim
      # exposes the three calls the overlay needs and Python loads it with ctypes
      # (sober_console/layershell.py). Same approach as afterglow's indicator.
      #
      # It MUST be built against the same Qt as PySide6 (the layer-shell plugin only loads
      # into the Qt it was built against); both come from the one nixpkgs here.
      mkLayerShell = pkgs: pkgs.stdenv.mkDerivation {
        pname = "sober-console-layershell";
        version = "0.1.0";
        src = ./native;
        nativeBuildInputs = [ pkgs.cmake ];
        buildInputs = [ pkgs.qt6.qtbase pkgs.kdePackages.layer-shell-qt ];
        dontWrapQtApps = true;
      };

      mkPackage = pkgs:
        let
          python = pkgs.python3;
          layerShell = mkLayerShell pkgs;
        in
        python.pkgs.buildPythonApplication {
          pname = "sober-console";
          version = "0.1.0";
          format = "pyproject";
          src = ./.;

          nativeBuildInputs = [
            python.pkgs.setuptools
            pkgs.makeWrapper
            pkgs.qt6.wrapQtAppsHook
          ];

          # qtbase + qtwayland so both the xcb and wayland platform plugins' own runtime
          # library dependencies (libxcb-cursor included -- Qt >= 6.5 needs it for xcb) are
          # in the closure. layer-shell-qt has to be in the closure too, so wrapQtApp puts
          # its wayland-shell-integration plugin on QT_PLUGIN_PATH.
          buildInputs = [
            pkgs.qt6.qtbase
            pkgs.qt6.qtwayland
            pkgs.xcb-util-cursor
            pkgs.kdePackages.layer-shell-qt
          ];

          propagatedBuildInputs = with python.pkgs; [
            pyside6
            evdev
            tomli-w
          ];

          pythonImportsCheck = [ "sober_console" ];

          # wrapQtAppsHook only reliably wraps ELF binaries, not the shebang entry-point
          # script buildPythonApplication generates, so wrapQtApp is called explicitly
          # (exactly as afterglow's flake does), with wrapProgram layered on top:
          #   kdotool  -- asked (as a subprocess) which window is focused / where it is
          #   SOBER_CONSOLE_LAYERSHELL_LIB -- where layershell.py finds the shim
          #   SOBER_CONSOLE_BIN -- how `sober-console watch` re-launches the UI process
          #                        through this same wrapper (so it keeps the Qt environment)
          postFixup = ''
            wrapQtApp "$out/bin/sober-console"
            wrapProgram "$out/bin/sober-console" \
              --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.kdotool pkgs.bash pkgs.coreutils ]} \
              --set-default SOBER_CONSOLE_LAYERSHELL_LIB ${layerShell}/lib/libsober_console_layershell.so \
              --set SOBER_CONSOLE_BIN "$out/bin/sober-console"
          '';

          # The pytest suite (tests/) needs Qt's offscreen platform; run it from
          # `nix develop` rather than in the sandboxed build.
          doCheck = false;

          meta = {
            description = "F9-style developer-console log overlay for Sober (Roblox on Linux)";
            homepage = "https://github.com/mr-tinkle-winkle/sober-console";
            mainProgram = "sober-console";
            platforms = pkgs.lib.platforms.linux;
          };
        };
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = mkPackage pkgs;
          layershell = mkLayerShell pkgs;
        });

      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python3.withPackages (ps: [
                ps.pyside6
                ps.evdev
                ps.tomli-w
                ps.pytest
                ps.setuptools
              ]))
              pkgs.qt6.qtbase
              pkgs.qt6.qtwayland
              pkgs.kdotool
            ];
            # A plain `nix develop` shell doesn't get automatic Qt wrapping the way the built
            # package does, so the plugin path is set by hand (same as afterglow's devShell).
            shellHook = ''
              export QT_PLUGIN_PATH="${pkgs.qt6.qtbase}/lib/qt-6/plugins:${pkgs.qt6.qtwayland}/lib/qt-6/plugins''${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
              echo "sober-console dev shell. Try: QT_QPA_PLATFORM=offscreen pytest -q   or   python -m sober_console run --standalone"
            '';
          };
        });

      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.services.sober-console;
          pkg = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        in
        {
          options.services.sober-console = {
            enable = lib.mkEnableOption "the Sober developer-console overlay (F9 log viewer)";

            user = lib.mkOption {
              type = lib.types.str;
              description = ''
                User the overlay runs for, and to grant /dev/input read access to (needed
                to see the F9 key press -- same requirement as afterglow and puppetry).
              '';
            };
          };

          config = lib.mkIf cfg.enable {
            environment.systemPackages = [ pkg ];

            # The toggle key is read from /dev/input/event* via evdev (passively: the key
            # still reaches the game).
            users.users.${cfg.user}.extraGroups = [ "input" ];

            # A per-user service, started with the graphical session so WAYLAND_DISPLAY is
            # set. It is only a tiny watcher: the real overlay process is started while
            # Sober is running and stopped when Sober closes.
            systemd.user.services.sober-console = {
              description = "sober-console: starts the Sober log overlay while Sober is running";
              wantedBy = [ "graphical-session.target" ];
              partOf = [ "graphical-session.target" ];
              after = [ "graphical-session.target" ];
              # user services exist for every user's manager; only run for the configured one
              unitConfig.ConditionUser = cfg.user;
              serviceConfig = {
                ExecStart = "${pkg}/bin/sober-console watch";
                Restart = "on-failure";
                RestartSec = 3;
              };
            };
          };
        };
    };
}
