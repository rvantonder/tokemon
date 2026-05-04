.PHONY: all build restart dist clean-dist-artifacts

all: build

build:
	pyinstaller -y Tokemon.spec

restart:
	-pkill -f Tokemon
	@sleep 1
	$(MAKE) build
	open dist/Tokemon.app

clean-dist-artifacts:
	rm -rf dist/Tokemon.app dist/Tokemon

dist: build
	@set -e; \
	latest_zip="$$(find dist -maxdepth 1 -type f -name 'Tokemon.*.zip' | sort -V | tail -n 1)"; \
	if [ -n "$$latest_zip" ]; then \
		latest_version="$${latest_zip##*/Tokemon.}"; \
		latest_version="$${latest_version%.zip}"; \
	else \
		latest_version="0.0.0"; \
	fi; \
	major="$${latest_version%%.*}"; \
	rest="$${latest_version#*.}"; \
	minor="$${rest%%.*}"; \
	patch="$${rest#*.}"; \
	next_patch="$$((patch + 1))"; \
	next_version="$${major}.$$minor.$$next_patch"; \
	archive="dist/Tokemon.$$next_version.zip"; \
	rm -f "$$archive"; \
	(cd dist && zip -qry "Tokemon.$$next_version.zip" Tokemon.app); \
	NEXT_VERSION="$$next_version" python3 -c 'import os, pathlib, re, sys; p = pathlib.Path("README.md"); v = os.environ["NEXT_VERSION"]; s = p.read_text(); s, n = re.subn(r"(releases/download/)\d+\.\d+\.\d+(/Tokemon\.)\d+\.\d+\.\d+(\.zip)", r"\g<1>" + v + r"\g<2>" + v + r"\g<3>", s, count=1); p.write_text(s); sys.exit(0 if n else "README.md release URL not found")'; \
	rm -rf dist/Tokemon.app dist/Tokemon; \
	printf 'Created %s\n' "$$archive"
