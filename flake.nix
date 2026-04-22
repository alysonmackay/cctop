{
  description = "cctop - a terminal dashboard for computational chemistry outputs";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.python3Packages.buildPythonApplication {
            pname = "compchem-cctop";
            version = "0.1.5";
            pyproject = true;

            src = self;

            build-system = [ pkgs.python3Packages.setuptools ];

            pythonImportsCheck = [ "cctop" ];

            meta = {
              description = "A minimal terminal dashboard for computational chemistry output files";
              homepage = "https://github.com/JEFF7712/cctop";
              license = pkgs.lib.licenses.mit;
              mainProgram = "cctop";
              platforms = pkgs.lib.platforms.linux;
            };
          };
        }
      );

      apps = forAllSystems (
        system:
        {
          default = {
            type = "app";
            program = "${self.packages.${system}.default}/bin/cctop";
          };
        }
      );
    };
}
