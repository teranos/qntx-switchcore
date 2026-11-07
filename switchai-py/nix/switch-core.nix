# switch-core, built from core/uv.lock with uv2nix.
#
# switch-core is the one distribution built from source (`editable = "."` in
# the lock); the other 127 locked distributions each have a wheel, and come
# from it by the hash uv recorded.
{
  lib,
  pyproject-nix,
  uv2nix,
  pyproject-build-systems,
  # sandbox-quantum/switch's tree; the Python project is its core/.
  src,
}:
let
  workspaceRoot = "${src}/core";

  workspace = uv2nix.lib.workspace.loadWorkspace { inherit workspaceRoot; };

  overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };

  forPkgs =
    {
      pkgs,
      # requires-python is >=3.12.
      python ? pkgs.python313,
    }:
    let
      pythonSet = (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.wheel
          overlay
        ]
      );

      switch-core-venv = pythonSet.mkVirtualEnv "switch-core-env" workspace.deps.default;

      # switch_core/main.py:2506 reads alembic.ini from the directory above the
      # package, which installed is site-packages itself; its script_location,
      # %(here)s/switch_core/migrations, is in the wheel.
      switch-core-alembic = pkgs.runCommand "switch-core-alembic" { } ''
        mkdir -p $out/${python.sitePackages}
        cp ${workspaceRoot}/alembic.ini $out/${python.sitePackages}/alembic.ini
      '';
    in
    {
      inherit pythonSet switch-core-venv switch-core-alembic;
    };
in
{
  inherit workspace forPkgs;
}
