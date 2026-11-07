{
  description = "switchai-py — pyre with switch-core baked in: Agent Rooms (QNTX ADR-057), switch-core in process";

  inputs = {
    # As otaripy: q runs Debian 13 with glibc 2.41, and nixos-25.11 builds
    # against glibc 2.40, which loads there.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    flake-utils.url = "github:numtide/flake-utils";
    # 「[Opus 5.5] pyre main, with @llm, @route, plugin_config (teranos/pyre#6) and @rooms (teranos/pyre#8).」
    pyre-src = {
      url = "github:teranos/pyre/a6c4cebc83bb3b932d6602fa739761d072ca2356";
      flake = false;
    };
    # The protocol pyre builds against: the ref pyre's Cargo.lock names.
    qntx-src = {
      url = "github:teranos/QNTX/8793497a197fc945676a11f2acc3f14d305136f7";
      flake = false;
    };
    # Switch Rooms' source; switch-core is its core/.
    switch = {
      url = "github:sandbox-quantum/switch/6a4631da1";
      flake = false;
    };
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      pyre-src,
      qntx-src,
      switch,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
    }:
    flake-utils.lib.eachSystem [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" ] (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        inherit (pkgs) lib;

        switchaiPyVersion = "0.2.0";

        switchCore =
          (import ./nix/switch-core.nix {
            inherit
              lib
              pyproject-nix
              uv2nix
              pyproject-build-systems
              ;
            src = switch;
          }).forPkgs
            { inherit pkgs; };

        # The interpreter's standard library and switch-core's locked
        # dependencies in one tree, python313 first so its bin/python3 is the
        # one joined, and the venv's pyvenv.cfg left out: the release tree
        # runs on PYTHONHOME, as otaripy's does.
        pythonEnv = pkgs.symlinkJoin {
          name = "switchai-py-env";
          paths = [
            pkgs.python313
            switchCore.switch-core-venv
          ];
          postBuild = ''
            rm $out/pyvenv.cfg
          '';
        };

        outputHashes = {
          "qntx-ats-0.2.44" = "sha256-bHkfuwmivE29uXjYqABTSNlUNu5hl41cwgnCPLRczbM=";
        };

        switchai-py = pkgs.rustPlatform.buildRustPackage {
          pname = "qntx-switchai-py-plugin";
          version = switchaiPyVersion;
          src = pyre-src;

          QNTX_PLUGIN_VERSION = switchaiPyVersion;

          cargoLock = {
            lockFile = "${pyre-src}/Cargo.lock";
            inherit outputHashes;
          };

          buildInputs = with pkgs; [
            protobuf
            pythonEnv
            openssl
          ];

          nativeBuildInputs = with pkgs; [
            pkg-config
            protobuf
            makeWrapper
          ];

          QNTX_PROTO_DIR = "${qntx-src}/plugin/grpc/protocol";
          PYO3_PYTHON = "${pythonEnv}/bin/python3";

          postInstall = ''
            mv $out/bin/pyre $out/bin/qntx-switchai-py-plugin
          ''
          + lib.optionalString pkgs.stdenv.isLinux ''
            patchelf --set-rpath "${lib.makeLibraryPath [ pythonEnv ]}:$(patchelf --print-rpath $out/bin/qntx-switchai-py-plugin)" \
              $out/bin/qntx-switchai-py-plugin
          ''
          + lib.optionalString pkgs.stdenv.isDarwin ''
            install_name_tool -add_rpath "${lib.makeLibraryPath [ pythonEnv ]}" \
              $out/bin/qntx-switchai-py-plugin
          ''
          + ''
            wrapProgram $out/bin/qntx-switchai-py-plugin \
              --prefix PYTHONPATH : "${pythonEnv}/${pkgs.python313.sitePackages}" \
              --add-flags "--name switchai-py"
          '';
        };
      in
      {
        packages = {
          default = switchai-py;
          inherit switchai-py;
          python = pythonEnv;
        };

        # bin/python3 is python313's own, not the venv's launcher, which execs
        # python313 by a store path a box without /nix lacks; and switch-core
        # imports, its app factory and its migrations' alembic.ini included.
        checks.interpreter = pkgs.runCommand "switchai-py-interpreter" { } ''
          ran=$(readlink -f ${pythonEnv}/bin/python3)
          own=$(readlink -f ${pkgs.python313}/bin/python3)
          echo "bin/python3 is $ran; python313's is $own"
          [ "$ran" = "$own" ]
          if [ -e ${pythonEnv}/pyvenv.cfg ]; then
            echo "the env carries pyvenv.cfg: $(cat ${pythonEnv}/pyvenv.cfg)"
            exit 1
          fi
          export PYTHONPATH=${pythonEnv}/${pkgs.python313.sitePackages}
          ${pythonEnv}/bin/python3 -c '
          import pathlib, switch_core, switch_core.main
          ini = pathlib.Path(switch_core.__file__).resolve().parent.parent / "alembic.ini"
          assert ini.is_file(), ini
          print("switch_core", switch_core.__file__, "alembic.ini", ini)
          ' > $out
        '';

        apps.default = {
          type = "app";
          program = "${switchai-py}/bin/qntx-switchai-py-plugin";
        };
      }
    );
}
