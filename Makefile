.PHONY: help test next-version build publish release

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  test          Run all tests"
	@echo "  next-version  Bump the patch version in pyproject.toml"
	@echo "  build         Build the package"
	@echo "  publish       Publish to PyPI"
	@echo "  release       next-version + build + publish"

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

release: next-version test build publish
