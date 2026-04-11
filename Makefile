.PHONY: all build restart

all: build

build:
	pyinstaller -y Tokmon.spec

restart: build
	-pkill -f Tokmon
	@sleep 1
	open dist/Tokmon.app
