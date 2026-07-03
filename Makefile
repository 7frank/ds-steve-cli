.PHONY: next-version build publish release

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

release: next-version build publish
