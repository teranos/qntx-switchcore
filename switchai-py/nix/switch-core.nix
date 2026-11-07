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
      # switch_core/main.py:2506 reads alembic.ini at
      # Path(__file__).resolve().parent.parent: resolve() follows the venv's
      # symlink to switch-core's own store path, so the file goes in that
      # package's site-packages. Its script_location,
      # %(here)s/switch_core/migrations, is in the package.
      overrides = final: prev: {
        switch-core = prev.switch-core.overrideAttrs (old: {
          postInstall = (old.postInstall or "") + ''
            cp ${workspaceRoot}/alembic.ini $out/${python.sitePackages}/alembic.ini
          '';
        });
      };

      pythonSet = (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.wheel
          overlay
          overrides
        ]
      );

      switch-core-venv = pythonSet.mkVirtualEnv "switch-core-env" workspace.deps.default;
    in
    {
      inherit pythonSet switch-core-venv;
    };
in
{
  inherit workspace forPkgs;
}
