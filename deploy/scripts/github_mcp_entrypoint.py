#!/usr/bin/env python3
"""GitHub MCP Sidecar Entrypoint Wrapper.

Mints a short-lived installation token from GitHub App credentials on startup,
writes it to a memory-backed file (/tmp/github_token), and execs the upstream
mcp server binary. Falls back to classic PAT authentication when variables are
missing or token generation fails (fail-soft mode).
"""

import logging
import os
import sys

# Configure logging to stderr so it surfaces in docker compose output.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] github_mcp_entrypoint: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("github_mcp_entrypoint")


def main() -> None:
    app_id = os.environ.get("GITHUB_APP_ID")
    installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
    key_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")
    token_file = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN_FILE", "/tmp/github_token")

    if app_id and installation_id and key_path:
        logger.info("GitHub App configuration detected. Minting installation token...")
        try:
            from lib.github_auth import mint_installation_token

            itok = mint_installation_token(
                app_id=app_id,
                installation_id=installation_id,
                private_key_path=key_path,
            )

            # Ensure output directory exists and write token
            os.makedirs(os.path.dirname(token_file), exist_ok=True)
            with open(token_file, "w") as f:
                f.write(itok.token)

            logger.info("GitHub App token successfully written to %s", token_file)

            # Force GITHUB_PERSONAL_ACCESS_TOKEN_FILE to point to our newly minted token
            os.environ["GITHUB_PERSONAL_ACCESS_TOKEN_FILE"] = token_file
            # Clear static GITHUB_PERSONAL_ACCESS_TOKEN if set to avoid priority override
            if "GITHUB_PERSONAL_ACCESS_TOKEN" in os.environ:
                del os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]

        except Exception as exc:
            logger.error(
                "Failed to mint GitHub App token: %s. Falling back to PAT authentication.",
                exc,
                exc_info=True,
            )
    else:
        logger.warning(
            "GitHub App credentials missing (need GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, "
            "and GITHUB_APP_PRIVATE_KEY_PATH). Falling back to PAT authentication."
        )

    # Exec the upstream binary
    upstream_binary = "/server/github-mcp-server"
    if not os.path.exists(upstream_binary):
        logger.critical("Upstream binary not found at %s", upstream_binary)
        sys.exit(1)

    args = [upstream_binary] + sys.argv[1:]
    logger.info("Executing upstream binary: %s", " ".join(args))

    # Flush all output buffers before exec
    sys.stdout.flush()
    sys.stderr.flush()

    try:
        os.execv(upstream_binary, args)
    except Exception as exc:
        logger.critical("Failed to exec %s: %s", upstream_binary, exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
