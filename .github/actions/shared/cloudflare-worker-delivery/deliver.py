from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def deliver(application: Path, configuration: Path, secrets: Path, workspace: Path) -> None:
    root = workspace.resolve()
    application = application.resolve()
    if not application.is_relative_to(root / ".workloads"):
        raise ValueError("Worker delivery requires a pinned .workloads checkout.")
    if not (application / "package.json").is_file():
        raise ValueError("The pinned application package is unavailable.")
    configuration = configuration.resolve(strict=True)
    secrets = secrets.resolve(strict=True)
    if not configuration.is_relative_to(application / ".wrangler"):
        raise ValueError("Generated Worker configuration must belong to the pinned application.")
    launcher = application / "node_modules" / "tsx" / "dist" / "cli.mjs"
    script = application / "scripts" / "deployment.ts"
    if not launcher.is_file() or not script.is_file():
        raise ValueError("The application must implement the guarded delivery contract.")
    for operation in ("migrate", "deploy"):
        arguments = ["node", str(launcher), str(script), operation, "--config", str(configuration)]
        if operation == "deploy":
            arguments += ["--secrets-file", str(secrets)]
        subprocess.run(arguments, cwd=application, check=True)
    subprocess.run(
        ["node", str(launcher), str(application / "scripts" / "verify-deployment.ts"), "--config", str(configuration)],
        cwd=application,
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application", default=os.environ.get("APPLICATION_DIRECTORY"))
    parser.add_argument("--configuration", default=os.environ.get("APPLICATION_CONFIGURATION"))
    parser.add_argument("--secrets", default=os.environ.get("APPLICATION_SECRETS_FILE"))
    arguments = parser.parse_args(argv)
    try:
        if not all((arguments.application, arguments.configuration, arguments.secrets)):
            raise ValueError("Application, configuration and private secrets paths are required.")
        deliver(
            Path(arguments.application),
            Path(arguments.configuration),
            Path(arguments.secrets),
            Path(os.environ.get("GITHUB_WORKSPACE", ".")),
        )
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Guarded Worker delivery failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
