1965 uv sync
1967 uv run steve setup env
1968 sops -d .encoded.workspaces.env > .workspaces.env
1985 set -a
1986 source .env
1987 source .workspaces.env
1988 set +a
1989 uv run steve buckets
1991 uv run steve jobs


  uv sync --refresh-package steve-cli

  If that doesn't work:

  uv cache clean && uv sync