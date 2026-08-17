{
  description = "Monitorize – turn your Android / Linux laptop into a secondary monitor for your Linux desktop";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    let
      # ── Overlay ────────────────────────────────────────────────────────
      overlay = final: prev: {
        monitorize = final.callPackage ./nix/package.nix { };
      };
    in
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ overlay ];
        };
      in
      {
        packages = {
          monitorize = pkgs.monitorize;
          default = pkgs.monitorize;
        };

        apps.default = {
          type = "app";
          program = "${pkgs.monitorize}/bin/monitorize";
        };

        devShells.default = pkgs.mkShell {
          inputsFrom = [ pkgs.monitorize ];
          packages = with pkgs; [
            python3Packages.pytest
          ];
        };
      }
    ) // {
      # ── Flake-level outputs (not per-system) ─────────────────────────
      overlays.default = overlay;

      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.programs.monitorize;
        in
        {
          options.programs.monitorize = {
            enable = lib.mkEnableOption "Monitorize – Android / Linux secondary monitor";
            openFirewall = lib.mkOption {
              type = lib.types.bool;
              default = true;
              description = "Whether to automatically open required firewall ports for Monitorize streaming and input.";
            };
          };

          config = lib.mkIf cfg.enable {
            nixpkgs.overlays = [ overlay ];
            environment.systemPackages = [ pkgs.monitorize ];
            boot.kernelModules = [ "uinput" ];

            # Dedicated group so only explicitly authorised users can create
            # virtual input devices via uinput.  Using the generic "input"
            # group would grant that capability to all input-group members,
            # which is overly permissive on multi-user systems.
            #
            # To grant a user access, add them to this group:
            #   users.users.<name>.extraGroups = [ "monitorize-input" ];
            users.groups.monitorize-input = { };

            services.udev.extraRules = ''
              KERNEL=="uinput", MODE="0660", GROUP="monitorize-input"
            '';

            networking.firewall = lib.mkIf cfg.openFirewall {
              allowedTCPPorts = [ 7110 7114 47989 47990 48989 49089 49090 ];
              allowedUDPPorts = [ 5353 7113 7117 ];
              allowedUDPPortRanges = [
                { from = 47998; to = 48010; }
                { from = 48998; to = 49010; }
                { from = 49098; to = 49110; }
              ];
            };
          };
        };
    };
}
