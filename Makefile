.PHONY: help test next-version build publish git-tag release

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  test          Run all tests"
	@echo "  next-version  Bump the patch version in pyproject.toml"
	@echo "  build         Build the package"
	@echo "  publish       Publish to PyPI"
	@echo "  git-tag       Create a git tag for the current version
  release       next-version + build + publish + git-tag"

test:
	uv run pytest tests/ -v

next-version:
	@current=$$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'); \
	major=$$(echo $$current | cut -d. -f1); \
	minor=$$(echo $$current | cut -d. -f2); \
	patch=$$(echo $$current | cut -d. -f3); \
	next=$$major.$$minor.$$((patch + 1)); \
	sed -i "s/^version = \"$$current\"/version = \"$$next\"/" pyproject.toml; \
	echo "Bumped version: $$current -> $$next"

build:
	rm -rf dist
	uv build

publish:
	uv publish --username __token__ --password $$(pass pypi/token)

git-tag:
	@version=$$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'); \
	git tag -a "v$$version" -m "Release v$$version"; \
	echo "Tagged: v$$version"

release: next-version test build publish git-tag
