{
  description = "Monitorize – Sunshine virtual displays for Moonlight clients";

  inputs = {
    self.submodules = true;
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    let
      # ── Overlay ────────────────────────────────────────────────────────
      overlay = final: prev: {
        monitorize = final.callPackage ./nix/package.nix {
          cudaSupport = final.stdenv.hostPlatform.system == "x86_64-linux";
        };
        monitorizeNoCuda = final.callPackage ./nix/package.nix {
          cudaSupport = false;
        };
      };
    in
    flake-utils.lib.eachSystem [ "x86_64-linux" "aarch64-linux" ] (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ overlay ];
          config.allowUnfreePredicate = pkg:
            nixpkgs.lib.hasPrefix "cuda" (nixpkgs.lib.getName pkg);
        };
      in
      {
        packages = {
          monitorize = pkgs.monitorize;
          monitorize-no-cuda = pkgs.monitorizeNoCuda;
          default = pkgs.monitorize;
        };

        apps.default = {
          type = "app";
          program = "${pkgs.monitorize}/bin/monitorize";
        };

        apps.no-cuda = {
          type = "app";
          program = "${pkgs.monitorizeNoCuda}/bin/monitorize";
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
            enable = lib.mkEnableOption "Monitorize Sunshine virtual displays";
            package = lib.mkOption {
              type = lib.types.package;
              default = pkgs.monitorize;
              defaultText = lib.literalExpression "pkgs.monitorize";
              description = "Monitorize package to install.";
            };
            openFirewall = lib.mkOption {
              type = lib.types.bool;
              default = true;
              description = "Whether to open the two embedded Sunshine port ranges and mDNS.";
            };
          };

          config = lib.mkIf cfg.enable {
            nixpkgs.overlays = [ overlay ];
            environment.systemPackages = [ cfg.package ];
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

            services.avahi = {
              enable = lib.mkDefault true;
              publish.enable = lib.mkDefault true;
              publish.userServices = lib.mkDefault true;
            };

            networking.firewall = lib.mkIf cfg.openFirewall {
              allowedTCPPorts = [ 47984 47989 47990 48010 49084 49089 49090 49110 ];
              allowedUDPPorts = [ 5353 ];
              allowedUDPPortRanges = [
                { from = 47998; to = 48010; }
                { from = 49098; to = 49110; }
              ];
            };
          };
        };
    };
}
